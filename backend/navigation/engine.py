"""
NavigationEngine — hardened mission execution loop with manual override
                   and perception-driven behavior arbitration.

What it does:
  Runs mission execution, telemetry ingestion, behavior/perception arbitration,
  motor command generation, and WebSocket telemetry broadcast.

Imports from:
  asyncio/queue/time/math/typing, mission.sequencer, navigation.kinematics,
  navigation.controller, navigation.manual, navigation.motor_writer,
  telemetry.source, perception.perception_manager, perception.hailo_runner,
  behaviour.behavior_manager, utils.geo, utils.logger, config.

Behavior:
  Existing navigation logic is preserved. HIL additions are additive: the last
  motor command is retained for /api/sim/motor_cmd and omega_cmd/speed_cmd are
  added to telemetry frames.

What changed from the original (everything else is preserved identically)
────────────────────────────────────────────────────────────────────────
1. BehaviorManager is inserted between the PurePursuitController output
   and the motor writer.  In NAVIGATE state the behavior manager is
   transparent — it passes pp_omega / pp_speed through unchanged.

2. PerceptionManager.update() is called once per tick (in _tick_auto)
   to drain the detection queue.  This is a single non-blocking call.

3. Telemetry frames carry new fields:
     behavior_state  — current BehaviorManager state name
     perception      — {obstacle_active, trash_active, ...} sub-dict
   These fields are additive; the frontend ignores unknown fields.

4. Manual override: BehaviorManager is bypassed entirely in MANUAL mode.
   The _tick_manual logic is unchanged.

5. A PerceptionManager and BehaviorManager instance are created in __init__
   and wired up.  The HailoRunner is started in setup() / run().

Operating modes (unchanged)
────────────────────────────
AUTO   Normal autonomous navigation with behavior arbitration.
MANUAL Operator has direct control; all perception/behavior is suspended.

All original hardening is preserved:
  - Wall-clock dt
  - Exception guard
  - Stale telemetry auto-pause
  - GPS validation gate
  - Max index jump cap
  - Load-while-running safety
  - Re-anchor after manual
"""

import asyncio
import queue
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
from perception.perception_manager import PerceptionManager
from perception.hailo_runner import HailoRunner
from behaviour.behavior_manager import BehaviorManager, BehaviorState
from utils.geo import haversine, bearing as calc_bearing, heading_error as calc_error
from utils.logger import log
from config import behavior_cfg, hailo_cfg

if TYPE_CHECKING:
    from fastapi import WebSocket

TICK_INTERVAL   = 0.1   # seconds (10 Hz)
STALE_LIMIT_S   = 3.0   # auto-pause after this many seconds of no telemetry
STALE_GPS_LIMIT = 20    # auto-pause after this many consecutive rejected GPS fixes
MAX_INDEX_JUMP  = 5     # max sequencer index advance per tick (GPS glitch guard)
# NOTE: re-anchor window is now read from behavior_cfg().REANCHOR_WINDOW (was 50, now 30)


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

        # ── Perception + behavior stack ───────────────────────────────────────
        self._detection_queue: queue.Queue = queue.Queue(
            maxsize=hailo_cfg().QUEUE_MAXSIZE
        )
        self.perception  = PerceptionManager(self._detection_queue)
        self.behavior    = BehaviorManager(reanchor_fn=self._reanchor)
        self._hailo      = HailoRunner(self._detection_queue)

        # ── WebSocket clients ─────────────────────────────────────────────────
        self._clients: Set['WebSocket'] = set()

        # ── State ─────────────────────────────────────────────────────────────
        self._mode:    OperatingMode = OperatingMode.AUTO
        self._running  = False
        self._paused   = False
        self._manual_dirty = False

        self._last_lat:     float = 0.0
        self._last_lng:     float = 0.0
        self._last_heading: float = 0.0
        self._last_valid_telem_t: float = 0.0
        self._mode_since: int = int(time.time() * 1000)
        self._last_motor_cmd = {
            'left_us': 1500,
            'right_us': 1500,
            'omega_cmd': 0.0,
            'speed_cmd': 0.0,
            'steering_label': 'stop',
            'behavior_state': 'navigate',
        }

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

    # ── Mission lifecycle (unchanged from original) ───────────────────────────

    def load_mission(self, mission) -> list:
        """Safe to call at any time — pauses/stops first."""
        if self._mode == OperatingMode.MANUAL:
            self.resume_auto()
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

    def resume(self):
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

    # ── Manual mode control (unchanged from original) ─────────────────────────

    def enter_manual(self):
        if self._mode == OperatingMode.MANUAL:
            return
        prev = self._mode
        self._mode        = OperatingMode.MANUAL
        self._mode_since  = int(time.time() * 1000)
        self._manual_dirty = True
        self._paused       = True
        self.manual_ctrl.stop()
        log.info("Entered MANUAL mode", from_mode=prev.value,
                 index=self.sequencer.current_index)

    def resume_auto(self):
        if self._mode == OperatingMode.AUTO:
            return
        self.manual_ctrl.stop()
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
        self._paused = True
        log.info("Returned to AUTO mode (paused — explicit resume/start required)")

    def _reanchor(self, lat: float, lng: float, from_index: int) -> int:
        """
        Find nearest path point within a forward search window.
        Called by both resume_auto() and BehaviorManager after excursions.
        """
        path  = self.sequencer.path
        total = len(path)
        if total == 0:
            return 0

        search_end = min(from_index + behavior_cfg().REANCHOR_WINDOW, total)
        best_idx  = from_index
        best_dist = float('inf')

        for i in range(from_index, search_end):
            d = haversine(lat, lng, path[i].lat, path[i].lng)
            if d < best_dist:
                best_dist = d
                best_idx  = i

        return best_idx

    # ── Manual motor commands (unchanged from original) ───────────────────────

    def manual_set_left(self, on: bool):      self.manual_ctrl.set_left(on)
    def manual_set_right(self, on: bool):     self.manual_ctrl.set_right(on)
    def manual_set_throttle(self, t: float):  self.manual_ctrl.set_throttle(t)

    # ── Health (extended with perception/behavior) ────────────────────────────

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
            # New perception + behavior health
            **self.perception.status_dict(),
            **self.behavior.status_dict(),
        }

    @property
    def last_motor_cmd(self) -> dict:
        return dict(self._last_motor_cmd)

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self):
        self._running = True
        last_tick_wall = time.monotonic()
        self.motor_writer.setup()
        self._hailo.start()   # start Hailo inference thread
        log.info("Navigation engine started (with perception stack)")

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
        self._hailo.stop()
        log.info("Navigation engine stopped")

    # ── Manual tick (unchanged from original) ────────────────────────────────

    async def _tick_manual(self, dt: float):
        """
        Manual tick: integrate kinematics from manual motor commands,
        read GPS/compass, and broadcast a telemetry frame.
        Auto navigation AND perception/behavior are fully suspended.
        """
        is_mock = isinstance(self.source, MockTelemetrySource)

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
            try:
                reading = await self.source.read()
                lat, lng, heading = reading.lat, reading.lng, reading.heading
            except (TelemetryTimeout, ValueError):
                lat, lng, heading = self._last_lat, self._last_lng, self._last_heading

        self._last_lat, self._last_lng, self._last_heading = lat, lng, heading

        motor    = self.manual_ctrl.to_motor_dict()
        steering = self.manual_ctrl.to_steering_label()
        nav_state = 'manual'
        omega_cmd, _ = self.manual_ctrl.compute()
        speed_cmd = (
            self.manual_ctrl.state.throttle * self.cfg.cruise_speed_mps
            if (self.manual_ctrl.state.left or self.manual_ctrl.state.right) else 0.0
        )
        left_us, right_us = self.motor_writer.manual_to_pwm(
            self.manual_ctrl.state.left,
            self.manual_ctrl.state.right,
            self.manual_ctrl.state.throttle,
        )
        self._last_motor_cmd = {
            'left_us': left_us,
            'right_us': right_us,
            'omega_cmd': round(omega_cmd, 3),
            'speed_cmd': round(speed_cmd, 3),
            'steering_label': steering,
            'behavior_state': 'manual',
        }

        target = self.sequencer.current_target
        t_lat  = target.lat  if target else lat
        t_lng  = target.lng  if target else lng
        t_lbl  = target.segment_label if target else '—'

        if not is_mock:
            await self.motor_writer.write(left_us, right_us)

        frame = {
            'lat':     round(lat, 7),
            'lng':     round(lng, 7),
            'heading': round(heading, 2),
            'speed':   round(speed_cmd, 3),
            'target_lat':  round(t_lat, 7),  'target_lng':  round(t_lng, 7),
            'lookahead_lat': round(lat, 7),  'lookahead_lng': round(lng, 7),
            'required_heading':      0.0,
            'heading_error':         0.0,
            'distance_to_target':    round(haversine(lat, lng, t_lat, t_lng), 2) if target else 0.0,
            'distance_to_lookahead': 0.0,
            'omega':                 round(omega_cmd, 3),
            'omega_cmd':             round(omega_cmd, 3),
            'speed_cmd':             round(speed_cmd, 3),
            'nav_state':             nav_state,
            'active_segment_label':  t_lbl,
            'active_segment_index':  self.sequencer.current_index,
            'total_path_points':     self.sequencer.total,
            'mission_progress':      round(self.sequencer.progress, 4),
            'gps_accepted':          True,
            'steering':              steering,
            'motor':                 motor,
            'motor_cmd':             self.last_motor_cmd,
            'mode':                  'manual',
            'mode_since':            self._mode_since,
            'source':                'mock' if is_mock else 'uart',
            'timestamp':             int(time.time() * 1000),
            # Perception/behavior fields — zeroed in manual mode
            'behavior_state':        'manual',
            'perception':            {'obstacle_active': False, 'trash_active': False},
        }
        await self._broadcast(frame)

    # ── Auto tick (perception + behavior arbitration inserted) ────────────────

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

        # ── 1. Read telemetry (unchanged) ─────────────────────────────────────
        gps_accepted = True
        if is_mock:
            reading = await self.source.read()
            lat, lng, heading = reading.lat, reading.lng, reading.heading
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

        # ── 2. Update perception (drain detection queue — non-blocking) ───────
        # CPU cost: a single queue.get_nowait loop + a few float ops.
        # Hailo-8L does the actual inference in its own OS thread.
        self.perception.update()

        # ── 3. Pure-pursuit (unchanged) ───────────────────────────────────────
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

        # ── 4. Pure-pursuit command (used as default / NAVIGATE command) ──────
        pp_omega = self.controller.compute_omega(lat, lng, heading, la_lat, la_lng)
        req_hdg  = calc_bearing(lat, lng, la_lat, la_lng)
        err      = calc_error(heading, req_hdg)
        pp_speed = self.controller.compute_speed(err)

        # ── 5. Behavior arbitration ───────────────────────────────────────────
        # BehaviorManager decides whether to use PP commands or override them.
        # In NAVIGATE state (default), cmd.omega_cmd == pp_omega exactly.
        beh_cmd = self.behavior.arbitrate(
            perception=self.perception,
            pp_omega=pp_omega,
            pp_speed=pp_speed,
            current_path_index=self.sequencer.current_index,
            current_lat=lat,
            current_lng=lng,
            current_segment_label=target.segment_label if target else '',
        )

        # If behavior re-anchored the sequencer index, update lookahead accordingly
        omega_cmd = beh_cmd.omega_cmd
        speed_cmd = beh_cmd.speed_cmd

        # ── 6. Kinematics (unchanged) ─────────────────────────────────────────
        self.kinematics.step(omega_cmd, dt, speed_cmd)
        if is_mock:
            self.source.set_position(
                self.kinematics.lat, self.kinematics.lng, self.kinematics.heading
            )

        # ── 7. Build telemetry frame (backward-compatible + new fields) ────────
        dist_to_target    = haversine(lat, lng, target.lat, target.lng)
        dist_to_lookahead = haversine(lat, lng, la_lat, la_lng)

        # Use behavior cmd for display; fall back to pp if in NAVIGATE
        steering = beh_cmd.steering_label()
        motor    = beh_cmd.to_motor_dict()
        left_us, right_us = self.motor_writer.omega_speed_to_pwm(
            omega_cmd, speed_cmd,
            self.cfg.max_speed_mps, self.cfg.max_turn_rate_dps,
        )
        self._last_motor_cmd = {
            'left_us': left_us,
            'right_us': right_us,
            'omega_cmd': round(omega_cmd, 3),
            'speed_cmd': round(speed_cmd, 3),
            'steering_label': steering,
            'behavior_state': self.behavior.state_name,
        }

        frame = {
            # ── Core fields (unchanged, existing frontend compatible) ───────
            'lat':     round(lat, 7),
            'lng':     round(lng, 7),
            'heading': round(heading, 2),
            'speed':   round(self.kinematics.speed, 3),
            'target_lat': round(target.lat, 7), 'target_lng': round(target.lng, 7),
            'lookahead_lat': round(la_lat, 7),  'lookahead_lng': round(la_lng, 7),
            'required_heading':      round(req_hdg, 2),
            'heading_error':         round(err, 2),
            'distance_to_target':    round(dist_to_target, 2),
            'distance_to_lookahead': round(dist_to_lookahead, 2),
            'omega':                 round(omega_cmd, 3),
            'omega_cmd':             round(omega_cmd, 3),
            'speed_cmd':             round(speed_cmd, 3),
            'nav_state':             self.sequencer.nav_state(),
            'active_segment_label':  target.segment_label,
            'active_segment_index':  self.sequencer.current_index,
            'total_path_points':     self.sequencer.total,
            'mission_progress':      round(self.sequencer.progress, 4),
            'gps_accepted':          gps_accepted,
            'steering':              steering,
            'motor':                 motor,
            'motor_cmd':             self.last_motor_cmd,
            'mode':                  'auto',
            'mode_since':            self._mode_since,
            'source':                'mock' if is_mock else 'uart',
            'timestamp':             int(time.time() * 1000),
            # ── New perception + behavior fields (additive, safe to ignore) ──
            'behavior_state':        self.behavior.state_name,
            'perception': {
                'obstacle_active':  self.perception.obstacle_active,
                'trash_active':     self.perception.trash_active,
                'obstacle_cx_norm': round(self.perception.obstacle.image_cx_norm, 3)
                                    if self.perception.obstacle else 0.0,
                'trash_cx_norm':    round(self.perception.trash.image_cx_norm, 3)
                                    if self.perception.trash else 0.0,
                'trash_area_frac':  round(self.perception.trash.area_frac, 4)
                                    if self.perception.trash else 0.0,
            },
        }

        # ── 8. Motor output (now via behavior cmd) ────────────────────────────
        if not is_mock:
            await self.motor_writer.write(left_us, right_us)

        await self._broadcast(frame)

    # ── Utility frames (unchanged from original) ──────────────────────────────

    def _idle_frame(self) -> dict:
        k  = self.kinematics
        st = 'paused' if self._paused else 'idle'
        return {
            'lat': k.lat, 'lng': k.lng, 'heading': k.heading, 'speed': 0.0,
            'target_lat': k.lat, 'target_lng': k.lng,
            'lookahead_lat': k.lat, 'lookahead_lng': k.lng,
            'required_heading': 0.0, 'heading_error': 0.0,
            'distance_to_target': 0.0, 'distance_to_lookahead': 0.0, 'omega': 0.0,
            'omega_cmd': 0.0, 'speed_cmd': 0.0,
            'nav_state':            st,
            'active_segment_label': 'Idle',
            'active_segment_index': 0,
            'total_path_points':    self.sequencer.total,
            'mission_progress':     self.sequencer.progress,
            'gps_accepted': True,
            'steering': 'stop',
            'motor': {'left': False, 'right': False, 'blinking': False},
            'motor_cmd': self.last_motor_cmd,
            'mode': 'auto', 'mode_since': self._mode_since,
            'source': 'mock', 'timestamp': int(time.time() * 1000),
            'behavior_state': 'navigate',
            'perception': {'obstacle_active': False, 'trash_active': False},
        }

    def _complete_frame(self) -> dict:
        return {**self._idle_frame(),
                'nav_state': 'completed', 'mission_progress': 1.0,
                'active_segment_label': 'Mission Complete',
                'behavior_state': 'navigate'}
