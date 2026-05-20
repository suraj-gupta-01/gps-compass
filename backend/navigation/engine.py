"""
NavigationEngine — Pure-Pursuit + Vessel Kinematics execution loop.

Architecture change from v1:
  v1: bearing(vessel → waypoint) → bang-bang discrete steering → teleport position
  v2: pure_pursuit(vessel, path) → proportional omega → kinematic integration → smooth position

Each 10 Hz tick:
  1. Compute lookahead point via PurePursuitController.find_lookahead().
  2. Advance sequencer index if lookahead has moved past waypoints (smooth rounding).
  3. Fallback: also call sequencer.update() for tight acceptance-radius check.
  4. Compute omega_cmd = Kp * heading_error(vessel_heading, bearing_to_lookahead).
  5. Compute speed_cmd (reduced when error is large).
  6. Integrate VesselKinematics.step(omega_cmd, speed_cmd, dt).
  7. Read heading from telemetry source (real compass on hardware).
  8. Compute steering label + motor state for frontend LEDs.
  9. Broadcast TelemetryFrame.

Mock mode:
  In mock mode the vessel's lat/lng/heading come entirely from the kinematic
  model — there is no external sensor.  The MockTelemetrySource is only used
  to add a small heading noise so the telemetry frame looks like real compass
  data.  On hardware, the real UART heading replaces the kinematic heading for
  the controller input (sensor-closed-loop), while lat/lng still come from GPS.
"""

import asyncio
import time
import math
from typing import Optional, Set, TYPE_CHECKING

from mission.sequencer import MissionSequencer
from navigation.kinematics import VesselKinematics, VesselConfig
from navigation.controller import PurePursuitController
from telemetry.source import BaseTelemetrySource, MockTelemetrySource
from utils.geo import haversine, bearing as calc_bearing, heading_error as calc_error

if TYPE_CHECKING:
    from fastapi import WebSocket

TICK_INTERVAL = 0.1   # seconds — 10 Hz navigation loop


class NavigationEngine:

    def __init__(self,
                 telemetry_source: Optional[BaseTelemetrySource] = None,
                 config: Optional[VesselConfig] = None):
        self.cfg       = config or VesselConfig()
        self.sequencer = MissionSequencer()
        self.source    = telemetry_source or MockTelemetrySource()
        self.kinematics = VesselKinematics(self.cfg)
        self.controller = PurePursuitController(self.cfg)

        self._clients: Set['WebSocket'] = set()
        self._running = False
        self._paused  = False

    # ── WebSocket client management ───────────────────────────────────────────

    def add_client(self, ws: 'WebSocket'):
        self._clients.add(ws)

    def remove_client(self, ws: 'WebSocket'):
        self._clients.discard(ws)

    async def _broadcast(self, payload: dict):
        dead = set()
        for ws in self._clients:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    # ── Mission lifecycle ─────────────────────────────────────────────────────

    def load_mission(self, mission) -> list:
        path = self.sequencer.load(mission)
        if self.sequencer.path:
            first = self.sequencer.path[0]
            self.kinematics.reset(first.lat, first.lng, heading=0.0)
            if isinstance(self.source, MockTelemetrySource):
                self.source.set_position(first.lat, first.lng, 0.0)
        return path

    def start(self):
        self._running = True
        self._paused  = False

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def reset(self):
        self._paused = False
        self.sequencer.reset()
        if self.sequencer.path:
            first = self.sequencer.path[0]
            self.kinematics.reset(first.lat, first.lng, heading=0.0)
            if isinstance(self.source, MockTelemetrySource):
                self.source.set_position(first.lat, first.lng, 0.0)

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self):
        self._running = True
        while self._running:
            await asyncio.sleep(TICK_INTERVAL)

            if self._paused or not self.sequencer.loaded:
                if self.sequencer.loaded:
                    await self._broadcast(self._idle_frame())
                continue

            if self.sequencer.is_complete:
                await self._broadcast(self._complete_frame())
                continue

            dt = TICK_INTERVAL

            # ── 1. Get current vessel position & heading ───────────────────────
            # Mock mode: use kinematic model position directly.
            # Real mode: lat/lng from GPS, heading from compass (UART).
            if isinstance(self.source, MockTelemetrySource):
                lat     = self.kinematics.lat
                lng     = self.kinematics.lng
                heading = self.kinematics.heading
            else:
                reading = await self.source.read()
                lat     = reading.lat
                lng     = reading.lng
                heading = reading.heading   # real compass reading

            # ── 2. Pure-pursuit: find lookahead point ─────────────────────────
            la_lat, la_lng, la_idx = self.controller.find_lookahead(
                lat, lng,
                self.sequencer.path,
                self.sequencer.current_index,
            )

            # ── 3. Advance sequencer via lookahead index ───────────────────────
            # This is what makes the vessel begin curving BEFORE the waypoint.
            self.sequencer.advance_to_index(la_idx)
            # Fallback: acceptance-radius check (handles end-of-path / short segs)
            self.sequencer.update(lat, lng)

            if self.sequencer.is_complete:
                await self._broadcast(self._complete_frame())
                continue

            target = self.sequencer.current_target

            # ── 4. Compute omega and speed commands ───────────────────────────
            omega_cmd = self.controller.compute_omega(
                lat, lng, heading, la_lat, la_lng
            )
            # Heading error to LOOKAHEAD (not to waypoint) — this is the true
            # error the controller is acting on.
            req_hdg = calc_bearing(lat, lng, la_lat, la_lng)
            err     = calc_error(heading, req_hdg)
            speed_cmd = self.controller.compute_speed(err)

            # ── 5. Integrate kinematics ───────────────────────────────────────
            self.kinematics.step(omega_cmd, dt, speed_cmd)

            if isinstance(self.source, MockTelemetrySource):
                self.source.set_position(
                    self.kinematics.lat,
                    self.kinematics.lng,
                    self.kinematics.heading,
                )

            # ── 6. Navigation metrics for telemetry frame ─────────────────────
            dist_to_target = haversine(lat, lng, target.lat, target.lng)
            dist_to_lookahead = haversine(lat, lng, la_lat, la_lng)

            steering = PurePursuitController.steering_from_omega(
                omega_cmd, self.cfg.max_turn_rate_dps
            )
            motor = PurePursuitController.motor_from_steering(
                steering, omega_cmd, self.cfg.max_turn_rate_dps
            )

            nav_st = self.sequencer.nav_state()

            source_label = 'mock' if isinstance(self.source, MockTelemetrySource) else 'uart'

            frame = {
                # Position
                'lat': round(lat, 7),
                'lng': round(lng, 7),
                'heading': round(heading, 2),
                'speed': round(self.kinematics.speed, 3),
                # Navigation
                'target_lat':          round(target.lat, 7),
                'target_lng':          round(target.lng, 7),
                'lookahead_lat':       round(la_lat, 7),
                'lookahead_lng':       round(la_lng, 7),
                'required_heading':    round(req_hdg, 2),
                'heading_error':       round(err, 2),
                'distance_to_target':  round(dist_to_target, 2),
                'distance_to_lookahead': round(dist_to_lookahead, 2),
                'omega':               round(omega_cmd, 3),
                # Mission state
                'nav_state':           nav_st,
                'active_segment_label': target.segment_label,
                'active_segment_index': self.sequencer.current_index,
                'total_path_points':   self.sequencer.total,
                'mission_progress':    round(self.sequencer.progress, 4),
                # Control
                'steering': steering,
                'motor':    motor,
                # Meta
                'source':    source_label,
                'timestamp': int(time.time() * 1000),
            }
            await self._broadcast(frame)

    # ── Utility frames ────────────────────────────────────────────────────────

    def _idle_frame(self) -> dict:
        k = self.kinematics
        st = 'paused' if self._paused else 'idle'
        return {
            'lat': k.lat, 'lng': k.lng, 'heading': k.heading, 'speed': 0.0,
            'target_lat': k.lat, 'target_lng': k.lng,
            'lookahead_lat': k.lat, 'lookahead_lng': k.lng,
            'required_heading': 0.0, 'heading_error': 0.0,
            'distance_to_target': 0.0, 'distance_to_lookahead': 0.0, 'omega': 0.0,
            'nav_state': st,
            'active_segment_label': 'Idle',
            'active_segment_index': 0,
            'total_path_points': self.sequencer.total,
            'mission_progress': self.sequencer.progress,
            'steering': 'stop',
            'motor': {'left': False, 'right': False, 'blinking': False},
            'source': 'mock',
            'timestamp': int(time.time() * 1000),
        }

    def _complete_frame(self) -> dict:
        return {
            **self._idle_frame(),
            'nav_state': 'completed',
            'mission_progress': 1.0,
            'active_segment_label': 'Mission Complete',
        }
