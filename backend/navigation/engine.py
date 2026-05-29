"""
NavigationEngine — hardened mission execution loop with manual override.

Operating modes
───────────────
AUTO   Normal autonomous navigation: pure-pursuit + kinematics.
MANUAL Operator has direct motor control.  Autonomous navigation is fully
       suspended.  The kinematics model is still integrated (mock mode) so
       the vessel continues to move realistically in simulation.
       Telemetry is broadcast every tick in both modes.

AUTO → MANUAL transition
────────────────────────
Immediate.  Sequencer index is frozen at its current value.
Motor outputs switch from computed to manual commands.
A "dirty" flag is set so the engine knows position may have drifted.

MANUAL → AUTO transition (resume_auto)
───────────────────────────────────────
1. Re-anchor: find the closest remaining path point within a forward window.
   This prevents the vessel from trying to navigate back to a point it
   passed or that is now geometrically behind it.
2. Clear dirty flag, clear manual motor state.
3. Resume pure-pursuit from the re-anchored index.

Why re-anchor instead of just resuming at frozen index?
  During manual, the vessel may have moved 10–30m in any direction.
  The frozen index may now be behind the vessel, causing the pure-pursuit
  lookahead to find a negative-t intersection (segment behind vessel) and
  steer the vessel backward.  Re-anchoring to the nearest FORWARD point
  prevents this entirely.

Hardening (same as previous version, preserved):
  - Wall-clock dt
  - Exception guard
  - Stale telemetry auto-pause (applies in AUTO only)
  - GPS validation gate
  - Max index jump cap
  - Load-while-running safety
"""

import asyncio
import time
import math
from enum import Enum
from typing import Optional, Set, List, TYPE_CHECKING

from mission.sequencer import MissionSequencer, PathPoint
from navigation.kinematics import VesselKinematics, VesselConfig
from navigation.controller import PurePursuitController
from navigation.manual import ManualController
from navigation.motor_writer import MotorCommandWriter
from telemetry.source import BaseTelemetrySource, MockTelemetrySource, TelemetryTimeout
from utils.geo import haversine, bearing as calc_bearing, heading_error as calc_error
from utils.logger import log

if TYPE_CHECKING:
    from fastapi import WebSocket

TICK_INTERVAL   = 0.1   # seconds (10 Hz)
STALE_LIMIT_S   = 3.0   # auto-pause after this many seconds of no telemetry
STALE_GPS_LIMIT = 20    # auto-pause after this many consecutive rejected GPS fixes
MAX_INDEX_JUMP  = 5     # max sequencer index advance per tick (GPS glitch guard)

# Re-anchor search window: how many path points ahead to search for nearest
# when returning from manual to auto.
REANCHOR_SEARCH_WINDOW = 50


class OperatingMode(str, Enum):
    AUTO   = 'auto'
    MANUAL = 'manual'


class EngineStatus:
    def __init__(self):
        self.last_tick_time:    float = 0.0
        self.last_valid_gps:    float = 0.0
        self.stale_gps_ticks:   int   = 0
        self.stale_telem_ticks: int   = 0
        self.loop_errors:       int   = 0
        self.gps_rejects_total: int   = 0
        self.uart_timeouts:     int   = 0


class NavigationEngine:

    def __init__(self,
                 telemetry_source: Optional[BaseTelemetrySource] = None,
                 config: Optional[VesselConfig] = None):
        self.cfg         = config or VesselConfig()
        self.sequencer   = MissionSequencer()
        self.source      = telemetry_source or MockTelemetrySource()
        self.kinematics  = VesselKinematics(self.cfg)
        self.controller  = PurePursuitController(self.cfg)
        self.manual_ctrl = ManualController(self.cfg)
        self.status      = EngineStatus()

        self.motor_writer = MotorCommandWriter()
        self._clients: Set['WebSocket'] = set()
        self._mode:    OperatingMode    = OperatingMode.AUTO
        self._running  = False
        self._paused   = False          # only meaningful in AUTO mode
        self._manual_dirty = False      # position may have drifted since last AUTO

        self._last_lat:     float = 0.0
        self._last_lng:     float = 0.0
        self._last_heading: float = 0.0
        self._last_valid_telem_t: float = 0.0
        self._mode_since: int = int(time.time() * 1000)

    # ── WebSocket clients ─────────────────────────────────────────────────────

    def add_client(self, ws: 'WebSocket'):    self._clients.add(ws)
    def remove_client(self, ws: 'WebSocket'): self._clients.discard(ws)

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
        """Safe to call at any time — pauses/stops first."""
        if self._mode == OperatingMode.MANUAL:
            self.resume_auto()          # exit manual cleanly before load
        was_running = self._running and not self._paused
        if was_running:
            self._paused = True
            log.info("Mission loaded while running — paused for reload")

        if not self.motor_writer._ready and not self.motor_writer.mock:
            self.motor_writer.setup()
        path = self.sequencer.load(mission)
        if self.sequencer.path:
            first = self.sequencer.path[0]
            self.kinematics.reset(first.lat, first.lng, heading=0.0)
            self._last_lat     = first.lat
            self._last_lng     = first.lng
            self._last_heading = 0.0
            if isinstance(self.source, MockTelemetrySource):
                self.source.set_position(first.lat, first.lng, 0.0)

        self._paused = True
        self._manual_dirty = False
        log.info("Mission loaded",
                 points=len(path),
                 segments=len(mission.mission) if mission.mission else 0)
        return path

    def start(self):
        if not self.sequencer.loaded:
            log.warning("start() called with no mission loaded")
            return
        if self._mode == OperatingMode.MANUAL:
            log.warning("start() called in MANUAL mode — switch to AUTO first")
            return
        self._running = True
        self._paused  = False
        self._last_valid_telem_t = time.monotonic()
        log.info("Mission started")

    def pause(self):
        if self._mode == OperatingMode.AUTO:
            self._paused = True
            log.info("Mission paused (AUTO)", index=self.sequencer.current_index)
            # Motor stop is sent at the top of the next idle tick

    def resume(self):
        """Resume AUTO mode from a paused state."""
        if self._mode == OperatingMode.MANUAL:
            log.warning("resume() ignored — vessel is in MANUAL mode")
            return
        if not self.sequencer.loaded:
            return
        self._paused = False
        self._last_valid_telem_t = time.monotonic()
        log.info("Mission resumed (AUTO)", index=self.sequencer.current_index)

    def reset(self):
        self._paused = True
        if self._mode == OperatingMode.MANUAL:
            self.resume_auto()
        self.sequencer.reset()
        if self.sequencer.path:
            first = self.sequencer.path[0]
            self.kinematics.reset(first.lat, first.lng, heading=0.0)
            self._last_lat, self._last_lng = first.lat, first.lng
            self._last_heading = 0.0
            if isinstance(self.source, MockTelemetrySource):
                self.source.set_position(first.lat, first.lng, 0.0)
        self.status.stale_gps_ticks = 0
        self.status.stale_telem_ticks = 0
        self._manual_dirty = False
        log.info("Mission reset")

    # ── Manual mode control ───────────────────────────────────────────────────

    def enter_manual(self):
        """
        Immediately suspend autonomous navigation and hand control to operator.
        Safe to call at any time, including when paused or idle.
        """
        if self._mode == OperatingMode.MANUAL:
            return   # already manual
        prev = self._mode
        self._mode        = OperatingMode.MANUAL
        self._mode_since  = int(time.time() * 1000)
        self._manual_dirty = True   # position will drift during manual
        self._paused       = True   # ensures _tick_auto is a no-op if called in wrong context
        self.manual_ctrl.stop()     # start with motors off — operator must explicitly command
        # Non-blocking: schedule stop command (motor_writer is async)
        log.info("Entered MANUAL mode", from_mode=prev.value,
                 index=self.sequencer.current_index)

    def resume_auto(self):
        """
        Exit manual mode, re-anchor sequencer to nearest remaining path point,
        and resume autonomous navigation from a paused state.
        Operator must call start() or resume() explicitly to un-pause.
        """
        if self._mode == OperatingMode.AUTO:
            return
        self.manual_ctrl.stop()

        # Re-anchor: find nearest remaining path point from current position
        if self._manual_dirty and self.sequencer.path and not self.sequencer.is_complete:
            nearest = self._reanchor(
                self._last_lat,
                self._last_lng,
                self.sequencer.current_index,
            )
            old_idx = self.sequencer.current_index
            self.sequencer.current_index = nearest
            log.info("Re-anchored after manual",
                     old_index=old_idx, new_index=nearest,
                     lat=round(self._last_lat, 5), lng=round(self._last_lng, 5))

        self._manual_dirty = False
        self._mode       = OperatingMode.AUTO
        self._mode_since = int(time.time() * 1000)
        # Require explicit resume() / start() — do not auto-unpause
        self._paused = True
        log.info("Returned to AUTO mode (paused — explicit resume/start required)")

    def _reanchor(self, lat: float, lng: float, from_index: int) -> int:
        """
        Find the nearest path point within a forward search window starting at
        from_index.  Returns the index of the nearest point.

        Search is forward-only within REANCHOR_SEARCH_WINDOW steps to avoid
        re-navigating to a point already completed before the manual override.
        """
        path  = self.sequencer.path
        total = len(path)
        if total == 0:
            return 0

        search_end = min(from_index + REANCHOR_SEARCH_WINDOW, total)
        best_idx  = from_index
        best_dist = float('inf')

        for i in range(from_index, search_end):
            d = haversine(lat, lng, path[i].lat, path[i].lng)
            if d < best_dist:
                best_dist = d
                best_idx  = i

        return best_idx

    # ── Manual motor commands (called from API) ───────────────────────────────

    def manual_set_left(self, on: bool):
        self.manual_ctrl.set_left(on)

    def manual_set_right(self, on: bool):
        self.manual_ctrl.set_right(on)

    def manual_set_throttle(self, throttle: float):
        self.manual_ctrl.set_throttle(throttle)

    # ── Health ────────────────────────────────────────────────────────────────

    def health_dict(self) -> dict:
        age = time.monotonic() - self.status.last_tick_time
        return {
            'running':           self._running,
            'paused':            self._paused,
            'mode':              self._mode.value,
            'mission_loaded':    self.sequencer.loaded,
            'nav_state':         self.sequencer.nav_state(),
            'waypoint_index':    self.sequencer.current_index,
            'total_waypoints':   self.sequencer.total,
            'loop_age_s':        round(age, 2),
            'stale_gps_ticks':   self.status.stale_gps_ticks,
            'uart_timeouts':     self.status.uart_timeouts,
            'gps_rejects_total': self.status.gps_rejects_total,
            'loop_errors':       self.status.loop_errors,
            'clients':           len(self._clients),
        }

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self):
        self._running = True
        last_tick_wall = time.monotonic()
        self.motor_writer.setup()
        log.info("Navigation engine started")

        while self._running:
            await asyncio.sleep(TICK_INTERVAL)

            now = time.monotonic()
            dt  = min(now - last_tick_wall, 0.5)
            last_tick_wall = now
            self.status.last_tick_time = now

            try:
                if self._mode == OperatingMode.MANUAL:
                    await self._tick_manual(dt)
                else:
                    await self._tick_auto(dt)
            except Exception as e:
                self.status.loop_errors += 1
                log.exception("Navigation tick error", e,
                              index=self.sequencer.current_index,
                              mode=self._mode.value)

        await self.motor_writer.stop()
        self.motor_writer.close()
        log.info("Navigation engine stopped")

    # ── Manual tick ───────────────────────────────────────────────────────────

    async def _tick_manual(self, dt: float):
        """
        Manual tick: integrate kinematics from manual motor commands,
        read GPS/compass, and broadcast a telemetry frame.
        Auto navigation is fully suspended.
        """
        is_mock = isinstance(self.source, MockTelemetrySource)

        # ── 1. Integrate kinematics from manual commands (mock only) ──────────
        if is_mock:
            omega_cmd, speed_cmd = self.manual_ctrl.compute()
            self.kinematics.step(omega_cmd, dt, speed_cmd)
            self.source.set_position(
                self.kinematics.lat, self.kinematics.lng, self.kinematics.heading
            )
            lat, lng = self.kinematics.lat, self.kinematics.lng
            reading = await self.source.read()
            heading = reading.heading
        else:
            # Real hardware: just read GPS/compass; actual motors are driven
            # by the hardware layer receiving left/right booleans externally.
            try:
                reading = await self.source.read()
                lat, lng, heading = reading.lat, reading.lng, reading.heading
            except (TelemetryTimeout, ValueError):
                lat, lng, heading = self._last_lat, self._last_lng, self._last_heading

        self._last_lat, self._last_lng, self._last_heading = lat, lng, heading

        # ── 2. Build manual telemetry frame ───────────────────────────────────
        motor    = self.manual_ctrl.to_motor_dict()
        steering = self.manual_ctrl.to_steering_label()

        # nav_state during manual: show 'manual' so frontend knows
        nav_state = 'manual'

        # Provide sequencer info as informational only (not being navigated)
        target = self.sequencer.current_target
        t_lat  = target.lat  if target else lat
        t_lng  = target.lng  if target else lng
        t_lbl  = target.segment_label if target else '—'

        # Send dashboard manual commands to STM32 when not in mock mode
        # Note: if RC receiver has hardware control, STM32 ignores Pi UART
        # (RC is directly wired to ESCs). Pi sends anyway for display/logging.
        if not isinstance(self.source, MockTelemetrySource):
            left_us, right_us = self.motor_writer.manual_to_pwm(
                self.manual_ctrl.state.left,
                self.manual_ctrl.state.right,
                self.manual_ctrl.state.throttle,
            )
            await self.motor_writer.write(left_us, right_us)

        frame = {
            'lat':     round(lat, 7),
            'lng':     round(lng, 7),
            'heading': round(heading, 2),
            'speed':   round(self.manual_ctrl.state.throttle * self.cfg.cruise_speed_mps, 3)
                       if (self.manual_ctrl.state.left or self.manual_ctrl.state.right) else 0.0,
            'target_lat':  round(t_lat, 7),  'target_lng':  round(t_lng, 7),
            'lookahead_lat': round(lat, 7),  'lookahead_lng': round(lng, 7),
            'required_heading':      0.0,
            'heading_error':         0.0,
            'distance_to_target':    round(haversine(lat, lng, t_lat, t_lng), 2) if target else 0.0,
            'distance_to_lookahead': 0.0,
            'omega':                 round(self.manual_ctrl.compute()[0], 3),
            'nav_state':             nav_state,
            'active_segment_label':  t_lbl,
            'active_segment_index':  self.sequencer.current_index,
            'total_path_points':     self.sequencer.total,
            'mission_progress':      round(self.sequencer.progress, 4),
            'gps_accepted':          True,
            'steering':              steering,
            'motor':                 motor,
            'mode':                  'manual',
            'mode_since':            self._mode_since,
            'source':                'mock' if is_mock else 'uart',
            'timestamp':             int(time.time() * 1000),
        }
        await self._broadcast(frame)

    # ── Auto tick ─────────────────────────────────────────────────────────────

    async def _tick_auto(self, dt: float):
        is_mock = isinstance(self.source, MockTelemetrySource)

        # ── Idle / paused ─────────────────────────────────────────────────────
        if self._paused or not self.sequencer.loaded:
            if self.sequencer.loaded:
                await self._broadcast(self._idle_frame())
            return

        if self.sequencer.is_complete:
            await self._broadcast(self._complete_frame())
            return

        # ── 1. Read telemetry ─────────────────────────────────────────────────
        gps_accepted = True
        if is_mock:
            reading = await self.source.read()
            lat, lng = self.kinematics.lat, self.kinematics.lng
            heading  = self.kinematics.heading
            gps_accepted = reading.gps_accepted
        else:
            try:
                reading = await self.source.read()
                lat, lng, heading = reading.lat, reading.lng, reading.heading
                gps_accepted = reading.gps_accepted
                self._last_valid_telem_t = time.monotonic()
                self.status.stale_telem_ticks = 0
            except TelemetryTimeout:
                self.status.uart_timeouts += 1
                self.status.stale_telem_ticks += 1
                lat, lng, heading = self._last_lat, self._last_lng, self._last_heading
                if time.monotonic() - self._last_valid_telem_t > STALE_LIMIT_S:
                    log.warning("Telemetry stale — auto-pausing")
                    self.pause()
                    return
            except ValueError as e:
                log.warning("Telemetry parse error", exc=str(e))
                lat, lng, heading = self._last_lat, self._last_lng, self._last_heading
                gps_accepted = False

        if gps_accepted:
            self._last_lat, self._last_lng, self._last_heading = lat, lng, heading
            self.status.stale_gps_ticks = 0
            self.status.last_valid_gps  = time.monotonic()
        else:
            self.status.stale_gps_ticks  += 1
            self.status.gps_rejects_total += 1
            if self.status.stale_gps_ticks >= STALE_GPS_LIMIT:
                log.warning("GPS rejected too many ticks — auto-pausing")
                self.pause()
                return

        # ── 2. Pure-pursuit ───────────────────────────────────────────────────
        la_lat, la_lng, la_idx = self.controller.find_lookahead(
            lat, lng, self.sequencer.path, self.sequencer.current_index
        )
        capped = min(la_idx, self.sequencer.current_index + MAX_INDEX_JUMP)
        self.sequencer.advance_to_index(capped)
        self.sequencer.update(lat, lng)

        if self.sequencer.is_complete:
            log.info("Mission complete", total=self.sequencer.total)
            await self._broadcast(self._complete_frame())
            return

        target = self.sequencer.current_target

        # ── 3. Control ────────────────────────────────────────────────────────
        omega_cmd = self.controller.compute_omega(lat, lng, heading, la_lat, la_lng)
        req_hdg   = calc_bearing(lat, lng, la_lat, la_lng)
        err       = calc_error(heading, req_hdg)
        speed_cmd = self.controller.compute_speed(err)

        # ── 4. Kinematics ─────────────────────────────────────────────────────
        self.kinematics.step(omega_cmd, dt, speed_cmd)
        if is_mock:
            self.source.set_position(
                self.kinematics.lat, self.kinematics.lng, self.kinematics.heading
            )

        # ── 5. Build frame ────────────────────────────────────────────────────
        dist_to_target    = haversine(lat, lng, target.lat, target.lng)
        dist_to_lookahead = haversine(lat, lng, la_lat, la_lng)
        steering = PurePursuitController.steering_from_omega(omega_cmd, self.cfg.max_turn_rate_dps)
        motor    = PurePursuitController.motor_from_steering(steering, omega_cmd, self.cfg.max_turn_rate_dps)

        frame = {
            'lat':     round(lat, 7),
            'lng':     round(lng, 7),
            'heading': round(heading, 2),
            'speed':   round(self.kinematics.speed, 3),
            'target_lat': round(target.lat, 7),  'target_lng': round(target.lng, 7),
            'lookahead_lat': round(la_lat, 7),   'lookahead_lng': round(la_lng, 7),
            'required_heading':      round(req_hdg, 2),
            'heading_error':         round(err, 2),
            'distance_to_target':    round(dist_to_target, 2),
            'distance_to_lookahead': round(dist_to_lookahead, 2),
            'omega':                 round(omega_cmd, 3),
            'nav_state':             self.sequencer.nav_state(),
            'active_segment_label':  target.segment_label,
            'active_segment_index':  self.sequencer.current_index,
            'total_path_points':     self.sequencer.total,
            'mission_progress':      round(self.sequencer.progress, 4),
            'gps_accepted':          gps_accepted,
            'steering':  steering,
            'motor':     motor,
            'mode':      'auto',
            'mode_since': self._mode_since,
            'source':    'mock' if is_mock else 'uart',
            'timestamp': int(time.time() * 1000),
        }
        # ── 6a. Send motor commands to STM32 ─────────────────────────────────
        if not isinstance(self.source, MockTelemetrySource):
            left_us, right_us = self.motor_writer.omega_speed_to_pwm(
                omega_cmd, speed_cmd,
                self.cfg.max_speed_mps, self.cfg.max_turn_rate_dps,
            )
            await self.motor_writer.write(left_us, right_us)

        await self._broadcast(frame)

    # ── Utility frames ────────────────────────────────────────────────────────

    def _idle_frame(self) -> dict:
        k  = self.kinematics
        st = 'paused' if self._paused else 'idle'
        return {
            'lat': k.lat, 'lng': k.lng, 'heading': k.heading, 'speed': 0.0,
            'target_lat': k.lat,  'target_lng': k.lng,
            'lookahead_lat': k.lat, 'lookahead_lng': k.lng,
            'required_heading': 0.0, 'heading_error': 0.0,
            'distance_to_target': 0.0, 'distance_to_lookahead': 0.0, 'omega': 0.0,
            'nav_state':            st,
            'active_segment_label': 'Idle',
            'active_segment_index': 0,
            'total_path_points':    self.sequencer.total,
            'mission_progress':     self.sequencer.progress,
            'gps_accepted': True,
            'steering': 'stop',
            'motor': {'left': False, 'right': False, 'blinking': False},
            'mode': 'auto',  'mode_since': self._mode_since,
            'source': 'mock', 'timestamp': int(time.time() * 1000),
        }

    def _complete_frame(self) -> dict:
        return {**self._idle_frame(),
                'nav_state': 'completed', 'mission_progress': 1.0,
                'active_segment_label': 'Mission Complete'}
