"""
behavior/behavior_manager.py — Explicit behavior arbitration state machine.

This is the single authority for all motor output decisions in AUTO mode.
NO other component may write to the motor controller directly — all motor
commands in AUTO mode flow exclusively through BehaviorManager.

State machine
─────────────

  ┌──────────────────────────────────────────────────────────────────────┐
  │                        Priority (high → low)                         │
  │  AVOID_OBSTACLE > COLLECT_TRASH > TRACK_TRASH > RETURN_TO_PATH      │
  │                          > NAVIGATE                                  │
  └──────────────────────────────────────────────────────────────────────┘

  States
  ──────
  NAVIGATE        Normal coverage-path following via pure-pursuit.
                  BehaviorManager passes control transparently to the engine's
                  existing _tick_auto logic (omega_cmd, speed_cmd from PPC).

  TRACK_TRASH     Obstacle-free + trash confirmed.  Visual servoing toward
                  trash.  TrashTracker computes omega/speed.

  COLLECT_TRASH   Trash fills frame (area_frac ≥ threshold).  Stop motors,
                  fire collection actuator, then → RETURN_TO_PATH.

  AVOID_OBSTACLE  Obstacle confirmed — highest priority.
                  Reactive potential-field avoidance.
                  Immediately pre-empts any lower-priority state.

  RETURN_TO_PATH  After trash collection or avoidance end.
                  Robot navigates back to the nearest remaining path point
                  using the existing re-anchor mechanism.

State transition diagram
────────────────────────

  NAVIGATE ──[trash]──────────────────────► TRACK_TRASH
      ▲                                          │
      │                                    [area >= thresh]
      │                                          ▼
      │                                   COLLECT_TRASH
      │                                          │
      │◄───────────────[collected]──────── RETURN_TO_PATH ◄──┐
      │                                                        │
      │    [obstacle]                             [clear]      │
      └────────────────────────────── AVOID_OBSTACLE ─────────┘
           (from ANY state, highest priority)

Obstacle pre-emption from TRACK_TRASH / COLLECT_TRASH:
  TRACK_TRASH  ──[obstacle]──► AVOID_OBSTACLE (trash aborted)
  COLLECT_TRASH──[obstacle]──► AVOID_OBSTACLE (collection aborted)

Integration with existing engine
─────────────────────────────────
BehaviorManager is called from _tick_auto() AFTER telemetry is read and
BEFORE motor commands are sent.  It receives the raw omega/speed from the
PurePursuitController and may replace them with its own commands.

The engine retains full responsibility for:
  - Telemetry reading (GPS, compass)
  - Telemetry broadcasting (WebSocket)
  - Sequencer advancement
  - Manual mode (BehaviorManager is bypassed entirely in MANUAL mode)
  - Mission lifecycle (load, start, pause, reset)
"""

import time
import math
from enum import Enum
from typing import Optional, Tuple

from config import behavior_cfg, obstacle_cfg, trash_cfg, BehaviorConfig
from perception.perception_manager import PerceptionManager
from perception.trash_tracker import TrashTracker, MotorCmd
from perception.detection import ObstacleObservation, TrashObservation
from utils.logger import log


class BehaviorState(str, Enum):
    NAVIGATE       = 'navigate'
    TRACK_TRASH    = 'track_trash'
    COLLECT_TRASH  = 'collect_trash'
    AVOID_OBSTACLE = 'avoid_obstacle'
    RETURN_TO_PATH = 'return_to_path'


# Explicit priority mapping (highest → lowest) for dwell guard.
# Replaces lexicographic .value comparison which did not match intended order.
PRIORITY = {
    BehaviorState.AVOID_OBSTACLE: 4,
    BehaviorState.COLLECT_TRASH:  3,
    BehaviorState.TRACK_TRASH:    2,
    BehaviorState.RETURN_TO_PATH: 1,
    BehaviorState.NAVIGATE:       0,
}


class BehaviorManager:
    """
    Single motor-command authority for AUTO mode.

    Usage (called from NavigationEngine._tick_auto each tick):

        cmd = behavior_mgr.arbitrate(
            perception=self.perception,
            pp_omega=omega_cmd,
            pp_speed=speed_cmd,
            current_path_index=self.sequencer.current_index,
        )
        # cmd is a MotorCmd — apply omega/speed to kinematics and motor_writer.

    The engine must call reanchor_callback when RETURN_TO_PATH requires
    re-anchoring.  Pass engine._reanchor as the callback.
    """

    def __init__(self, reanchor_fn=None):
        """
        reanchor_fn: callable(lat, lng, from_index) -> int
            Should be NavigationEngine._reanchor.
            Called when RETURN_TO_PATH needs to re-anchor the sequencer.
        """
        self._state:    BehaviorState = BehaviorState.NAVIGATE
        self._prev_state: BehaviorState = BehaviorState.NAVIGATE
        self._state_since: float = time.monotonic()

        self._bcfg = behavior_cfg()
        self._ocfg = obstacle_cfg()
        self._tcfg = trash_cfg()

        self._trash_tracker = TrashTracker()
        self._reanchor_fn   = reanchor_fn

        # Obstacle avoidance decay counter
        self._obstacle_clear_count: int = 0
        self._obstacle_clear_needed: int = self._ocfg.OBSTACLE_CLEAR_FRAMES

        # Return-to-path state
        self._return_target_index: Optional[int] = None

        # Per-lane diversion counter
        self._deviations_this_lane: int = 0
        self._last_lane_label: str = ''

        # Diagnostics
        self._state_transitions: int = 0

    # ── Main arbitration entry point ──────────────────────────────────────────

    def arbitrate(
        self,
        perception: PerceptionManager,
        pp_omega:   float,     # pure-pursuit angular velocity (deg/s)
        pp_speed:   float,     # pure-pursuit speed (m/s)
        current_path_index: int,
        current_lat: float = 0.0,
        current_lng: float = 0.0,
        current_segment_label: str = '',
    ) -> MotorCmd:
        """
        Core arbitration logic.  Called every navigation tick.

        Returns a MotorCmd that the engine should apply.
        If the current state is NAVIGATE, the returned cmd contains the
        unmodified pure-pursuit omega/speed so the engine behaves identically
        to the pre-perception codebase.
        """
        obs   = perception.obstacle
        trash = perception.trash

        # ── 1. Update per-lane deviation counter ──────────────────────────────
        if current_segment_label != self._last_lane_label:
            self._last_lane_label    = current_segment_label
            self._deviations_this_lane = 0

        # ── 2. Determine next state (priority cascade) ────────────────────────
        next_state = self._select_state(obs, trash, current_path_index)

        # ── 3. Apply dwell guard (prevent micro-oscillations) ─────────────────
        dwell = time.monotonic() - self._state_since
        if (next_state != self._state and
                dwell < self._bcfg.MIN_STATE_DWELL_S and
                PRIORITY[next_state] < PRIORITY[self._state]):
            # Lower-priority state wants to fire; respect dwell time
            next_state = self._state

        # ── 4. Execute state transition if needed ─────────────────────────────
        if next_state != self._state:
            self._transition(next_state, current_path_index, current_lat, current_lng)

        # ── 5. Execute current state → produce motor command ──────────────────
        return self._execute(
            obs, trash, pp_omega, pp_speed, current_path_index,
            current_lat, current_lng,
        )

    # ── State selection (priority logic) ─────────────────────────────────────

    def _select_state(
        self,
        obs:   Optional[ObstacleObservation],
        trash: Optional[TrashObservation],
        path_index: int,
    ) -> BehaviorState:
        """
        Pure function: given current perceptions, return the desired next state.
        Priority order: AVOID_OBSTACLE > COLLECT_TRASH > TRACK_TRASH > NAVIGATE
        """
        # Priority 1: Obstacle — pre-empts everything
        if obs is not None and obs.width_frac >= self._ocfg.OBSTACLE_WIDTH_FRAC_THRESHOLD:
            self._obstacle_clear_count = 0
            return BehaviorState.AVOID_OBSTACLE

        # If we WERE avoiding obstacle, require N clear frames before exiting
        if self._state == BehaviorState.AVOID_OBSTACLE:
            if obs is None or obs.width_frac < self._ocfg.OBSTACLE_WIDTH_FRAC_THRESHOLD:
                self._obstacle_clear_count += 1
            else:
                self._obstacle_clear_count = 0
            if self._obstacle_clear_count < self._obstacle_clear_needed:
                return BehaviorState.AVOID_OBSTACLE
            # Obstacle cleared → go to return-to-path
            return BehaviorState.RETURN_TO_PATH

        # Priority 2: Collection trigger (already tracking, area threshold hit)
        if (self._state in (BehaviorState.TRACK_TRASH, BehaviorState.COLLECT_TRASH)
                and trash is not None
                and self._trash_tracker.collection_triggered(trash)):
            return BehaviorState.COLLECT_TRASH

        # Priority 3: Trash tracking
        if (trash is not None
                and self._state not in (BehaviorState.COLLECT_TRASH,
                                        BehaviorState.RETURN_TO_PATH)
                and self._deviations_this_lane < self._tcfg.MAX_DEVIATIONS_PER_LANE):
            return BehaviorState.TRACK_TRASH

        # Trash gone while tracking
        if (self._state == BehaviorState.TRACK_TRASH and trash is None):
            if self._trash_tracker.active:
                # Still within timeout; continue briefly
                return BehaviorState.TRACK_TRASH
            return BehaviorState.RETURN_TO_PATH

        # RETURN_TO_PATH stays until re-anchor complete (handled in _execute)
        if self._state == BehaviorState.RETURN_TO_PATH:
            return BehaviorState.RETURN_TO_PATH

        return BehaviorState.NAVIGATE

    # ── State transitions ─────────────────────────────────────────────────────

    def _transition(
        self,
        new_state:  BehaviorState,
        path_index: int,
        lat:        float,
        lng:        float,
    ) -> None:
        old = self._state
        self._prev_state  = old
        self._state       = new_state
        self._state_since = time.monotonic()
        self._state_transitions += 1

        log.info("BehaviorManager state transition",
                 from_state=old.value,
                 to_state=new_state.value,
                 path_index=path_index)

        if new_state == BehaviorState.TRACK_TRASH:
            self._trash_tracker.begin_approach(path_index)
            self._deviations_this_lane += 1

        elif new_state == BehaviorState.AVOID_OBSTACLE:
            # Immediately abort any trash approach
            self._trash_tracker.abort()
            self._obstacle_clear_count = 0

        elif new_state == BehaviorState.RETURN_TO_PATH:
            # Remember where we need to return to
            self._return_target_index = (
                self._trash_tracker.diversion_path_index or path_index
            )
            if self._reanchor_fn is not None and self._return_target_index is not None:
                new_idx = self._reanchor_fn(lat, lng, self._return_target_index)
                log.info("BehaviorManager re-anchor",
                         from_index=self._return_target_index,
                         to_index=new_idx)
                self._return_target_index = new_idx

        elif new_state == BehaviorState.NAVIGATE:
            self._obstacle_clear_count = 0

    # ── State execution ───────────────────────────────────────────────────────

    def _execute(
        self,
        obs:   Optional[ObstacleObservation],
        trash: Optional[TrashObservation],
        pp_omega:   float,
        pp_speed:   float,
        path_index: int,
        lat:        float,
        lng:        float,
    ) -> MotorCmd:
        """Produce motor command for the current state."""

        state = self._state

        # ── NAVIGATE: pass through pure-pursuit commands unchanged ─────────────
        if state == BehaviorState.NAVIGATE:
            return self._pp_to_cmd(pp_omega, pp_speed)

        # ── TRACK_TRASH: visual servoing ──────────────────────────────────────
        if state == BehaviorState.TRACK_TRASH:
            return self._trash_tracker.update(trash)

        # ── COLLECT_TRASH: stop and collect ───────────────────────────────────
        if state == BehaviorState.COLLECT_TRASH:
            if not self._trash_tracker.collected:
                self._trash_tracker.fire_collection()
            # After firing, transition to RETURN_TO_PATH next tick.
            # _transition() performs equivalent re-anchor logic for RETURN_TO_PATH.
            self._transition(BehaviorState.RETURN_TO_PATH, path_index, lat, lng)
            return MotorCmd(omega_cmd=0.0, speed_cmd=0.0,
                            left_on=False, right_on=False)

        # ── AVOID_OBSTACLE: reactive potential-field steering ─────────────────
        if state == BehaviorState.AVOID_OBSTACLE:
            return self._obstacle_avoidance_cmd(obs, pp_speed)

        # ── RETURN_TO_PATH: resume pure-pursuit toward re-anchored index ───────
        if state == BehaviorState.RETURN_TO_PATH:
            # Navigation engine will use the current sequencer index (which was
            # re-anchored during transition).  We pass pure-pursuit commands
            # through unmodified — the engine already computed these toward the
            # re-anchored target.
            # When engine confirms we are navigating normally, transition back.
            self._transition(BehaviorState.NAVIGATE, path_index, lat, lng)
            return self._pp_to_cmd(pp_omega, pp_speed)

        # Fallback
        return self._pp_to_cmd(pp_omega, pp_speed)

    # ── Obstacle avoidance ────────────────────────────────────────────────────

    def _obstacle_avoidance_cmd(
        self,
        obs:      Optional[ObstacleObservation],
        pp_speed: float,
    ) -> MotorCmd:
        """
        Reactive potential-field obstacle avoidance.

        desired_heading_vector: straight ahead  (normalised: 0, +1)
        repulsion_vector:       away from obstacle centre

        Combined steering = GAIN * (obstacle_cx_norm)
        Positive cx_norm (obstacle right) → steer LEFT (negative omega)
        Negative cx_norm (obstacle left)  → steer RIGHT (positive omega)

        Hard stop if obstacle is very close (width_frac > STOP_THRESHOLD).
        """
        ocfg = self._ocfg

        if obs is None:
            # Should not reach here; return gentle forward
            return MotorCmd(omega_cmd=0.0, speed_cmd=pp_speed * 0.5,
                            left_on=True, right_on=True)

        # Hard stop check
        if obs.width_frac >= ocfg.OBSTACLE_STOP_WIDTH_FRAC:
            return MotorCmd(omega_cmd=0.0, speed_cmd=0.0,
                            left_on=False, right_on=False, blinking=True)

        # Repulsion: obstacle on right (cx_norm > 0) → steer left (omega < 0)
        omega_cmd = -ocfg.OBSTACLE_REPULSION_GAIN * obs.image_cx_norm

        # Speed reduction proportional to obstacle size
        proximity = min(obs.width_frac / ocfg.OBSTACLE_STOP_WIDTH_FRAC, 1.0)
        speed_cmd = pp_speed * ocfg.OBSTACLE_SPEED_FACTOR * (1.0 - proximity * 0.5)

        # Derive discrete motor states
        if omega_cmd > 2.0:
            left_on, right_on = True, False   # hard right turn (obstacle on left)
        elif omega_cmd < -2.0:
            left_on, right_on = False, True   # hard left turn (obstacle on right)
        else:
            left_on, right_on = True, True    # slight adjust, keep moving

        return MotorCmd(
            omega_cmd=omega_cmd,
            speed_cmd=speed_cmd,
            left_on=left_on,
            right_on=right_on,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _pp_to_cmd(omega: float, speed: float) -> MotorCmd:
        """Wrap pure-pursuit outputs into a MotorCmd."""
        if abs(omega) < 1.5:
            left_on, right_on = True, True
        elif omega > 0:
            left_on, right_on = True, False
        else:
            left_on, right_on = False, True

        return MotorCmd(
            omega_cmd=omega,
            speed_cmd=speed,
            left_on=left_on,
            right_on=right_on,
        )

    # ── Properties for engine / telemetry ────────────────────────────────────

    @property
    def state(self) -> BehaviorState:
        return self._state

    @property
    def state_name(self) -> str:
        return self._state.value

    def status_dict(self) -> dict:
        dwell = round(time.monotonic() - self._state_since, 2)
        return {
            'behavior_state':       self._state.value,
            'behavior_dwell_s':     dwell,
            'behavior_transitions': self._state_transitions,
            'deviations_this_lane': self._deviations_this_lane,
            **self._trash_tracker.status_dict(),
        }
