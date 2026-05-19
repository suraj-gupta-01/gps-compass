"""
Navigation engine — runs the mission execution loop.

Each tick:
  1. Reads position + heading from telemetry source.
  2. Calls sequencer.update() to advance waypoint if arrived.
  3. Computes bearing + heading error to current target.
  4. Determines steering command and motor state.
  5. Broadcasts TelemetryFrame over WebSocket.
"""

import asyncio
import time
import math
from typing import Optional, Set, TYPE_CHECKING

from mission.sequencer import MissionSequencer
from telemetry.source import BaseTelemetrySource, MockTelemetrySource
from utils.geo import haversine, bearing as calc_bearing, heading_error as calc_error

if TYPE_CHECKING:
    from fastapi import WebSocket

# ── Steering thresholds (degrees) ─────────────────────────────────────────────
THRESHOLD_FORWARD    =  8   # within ± 8°  → go forward
THRESHOLD_GENTLE     = 20   # within ± 20° → gentle turn
# beyond 20° → hard turn / large correction

TICK_INTERVAL = 0.1  # seconds (10 Hz)

# Simulated speed for mock mode: metres per tick
MOCK_SPEED_M_PER_TICK = 1.5


def _motor_state(steering: str) -> dict:
    if steering == 'forward':
        return {'left': True,  'right': True,  'blinking': False}
    if steering == 'turn_left':
        return {'left': False, 'right': True,  'blinking': False}
    if steering == 'turn_right':
        return {'left': True,  'right': False, 'blinking': False}
    if steering == 'large_correction':
        return {'left': False, 'right': False, 'blinking': True}
    # stop
    return {'left': False, 'right': False, 'blinking': False}


def _steering(error_deg: float) -> str:
    if abs(error_deg) <= THRESHOLD_FORWARD:
        return 'forward'
    if abs(error_deg) <= THRESHOLD_GENTLE:
        return 'turn_right' if error_deg > 0 else 'turn_left'
    if abs(error_deg) <= 90:
        return 'turn_right' if error_deg > 0 else 'turn_left'
    return 'large_correction'


class NavigationEngine:
    def __init__(self, telemetry_source: Optional[BaseTelemetrySource] = None):
        self.sequencer = MissionSequencer()
        self.source: BaseTelemetrySource = telemetry_source or MockTelemetrySource()
        self._clients: Set['WebSocket'] = set()
        self._running = False
        self._paused  = False
        # Mock simulation position (for MockTelemetrySource)
        self._sim_lat: float = 0.0
        self._sim_lng: float = 0.0
        self._sim_heading: float = 0.0

    # ── Client management ─────────────────────────────────────────────────────

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

    # ── Mission control ───────────────────────────────────────────────────────

    def load_mission(self, mission) -> list:
        path = self.sequencer.load(mission)
        if path:
            first = self.sequencer.path[0]
            self._sim_lat = first.lat
            self._sim_lng = first.lng
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
            self._sim_lat, self._sim_lng = first.lat, first.lng
            if isinstance(self.source, MockTelemetrySource):
                self.source.set_position(first.lat, first.lng, 0.0)

    # ── Main execution loop ───────────────────────────────────────────────────

    async def run(self):
        """Run forever; call from FastAPI lifespan or background task."""
        self._running = True
        while self._running:
            await asyncio.sleep(TICK_INTERVAL)

            if self._paused or not self.sequencer.loaded:
                # Still broadcast idle/paused state so the UI stays alive
                if self.sequencer.loaded:
                    await self._broadcast(self._idle_frame())
                continue

            if self.sequencer.is_complete:
                await self._broadcast(self._complete_frame())
                continue

            # 1. Read telemetry
            reading = await self.source.read()

            # 2. In mock mode: advance simulated position toward current target
            if isinstance(self.source, MockTelemetrySource) and self.sequencer.current_target:
                target = self.sequencer.current_target
                req_hdg = calc_bearing(self._sim_lat, self._sim_lng, target.lat, target.lng)
                dist = haversine(self._sim_lat, self._sim_lng, target.lat, target.lng)
                step = min(MOCK_SPEED_M_PER_TICK, dist)
                if dist > 0:
                    self._sim_lat += step * math.cos(math.radians(req_hdg)) / 110540
                    self._sim_lng += step * math.sin(math.radians(req_hdg)) / (
                        111320 * math.cos(math.radians(self._sim_lat)))
                self._sim_heading = req_hdg
                self.source.set_position(self._sim_lat, self._sim_lng, req_hdg)
                # re-read to get updated position
                reading = await self.source.read()

            lat, lng, heading = reading.lat, reading.lng, reading.heading

            # 3. Advance sequencer
            target = self.sequencer.update(lat, lng)
            if target is None:
                await self._broadcast(self._complete_frame())
                continue

            # 4. Navigation calculations
            req_hdg  = calc_bearing(lat, lng, target.lat, target.lng)
            err      = calc_error(heading, req_hdg)
            dist     = haversine(lat, lng, target.lat, target.lng)
            steering = _steering(err)
            motor    = _motor_state(steering)
            nav_st   = self.sequencer.nav_state()
            if self._paused:
                nav_st = 'paused'

            frame = {
                'lat': lat, 'lng': lng,
                'heading': heading,
                'target_lat': target.lat, 'target_lng': target.lng,
                'required_heading': req_hdg,
                'heading_error': err,
                'distance_to_target': dist,
                'nav_state': nav_st,
                'active_segment_label': target.segment_label,
                'active_segment_index': self.sequencer.current_index,
                'total_path_points': self.sequencer.total,
                'mission_progress': self.sequencer.progress,
                'steering': steering,
                'motor': motor,
                'source': reading.source,
                'timestamp': int(time.time() * 1000),
            }
            await self._broadcast(frame)

    def _idle_frame(self) -> dict:
        pos = self.sequencer.path[0] if self.sequencer.path else None
        st = 'paused' if self._paused else 'idle'
        return {
            'lat': pos.lat if pos else 0, 'lng': pos.lng if pos else 0,
            'heading': self._sim_heading,
            'target_lat': pos.lat if pos else 0, 'target_lng': pos.lng if pos else 0,
            'required_heading': 0, 'heading_error': 0, 'distance_to_target': 0,
            'nav_state': st,
            'active_segment_label': 'Idle', 'active_segment_index': 0,
            'total_path_points': self.sequencer.total,
            'mission_progress': self.sequencer.progress,
            'steering': 'stop', 'motor': {'left': False, 'right': False, 'blinking': False},
            'source': 'mock', 'timestamp': int(time.time() * 1000),
        }

    def _complete_frame(self) -> dict:
        return {**self._idle_frame(), 'nav_state': 'completed',
                'mission_progress': 1.0, 'active_segment_label': 'Mission Complete'}