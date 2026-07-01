"""
perception/trash_tracker.py — Vision-guided trash approach controller.

This module implements VISUAL SERVOING — the robot steers purely based on
where the trash appears in the camera frame, with NO GPS coordinate involved
in the approach or collection decision.

Algorithm
─────────
  frame_centre_x = 0  (normalised; -1 = left, +1 = right)
  error_x = trash_observation.image_cx_norm   (normalised pixel error)

  omega_cmd = KP * error_x                  (deg/s, positive = turn right)

  Approach:
    - Robot drives forward at TRASH_APPROACH_SPEED
    - Simultaneously steers to centre the trash horizontally
    - When bbox area fraction >= COLLECTION_AREA_FRAC → fire collection event

Collection trigger
──────────────────
  area_frac = bbox_w * bbox_h / (frame_w * frame_h)

  As the robot approaches, the trash grows in the frame.  When area_frac
  reaches COLLECTION_AREA_FRAC the robot is close enough to collect.

  This is more robust than GPS distance because:
    1. GPS has ~1-3m error; camera pixel coverage has sub-centimetre precision
       at close range.
    2. Works in mock mode without any position estimate.
    3. Automatically adapts to different trash sizes.

Return-to-path
──────────────
  After collection (or timeout), TrashTracker records the path index at
  which diversion started.  The BehaviorManager uses this to call
  engine._reanchor() and rejoin the coverage path cleanly.

State transitions driven by BehaviorManager:
  begin_approach(path_index)  — called when TRACK_TRASH starts
  update(trash_obs)           — called every tick → returns MotorCmd
  collection_triggered()      — True when area threshold is reached
  abort()                     — called on obstacle interrupt
"""

import time
from dataclasses import dataclass
from typing import Optional

from config import trash_cfg, obstacle_cfg
from perception.detection import TrashObservation
from utils.logger import log


@dataclass
class MotorCmd:
    """
    Unified motor command from any behaviour controller.
    Mirrors the structure expected by NavigationEngine._apply_motor_cmd().
    """
    omega_cmd:  float   # deg/s, positive = turn right
    speed_cmd:  float   # m/s
    left_on:    bool    # discrete motor state (for dashboard display)
    right_on:   bool
    blinking:   bool = False

    def steering_label(self) -> str:
        if not self.left_on and not self.right_on:
            return 'stop'
        if self.left_on and self.right_on:
            return 'forward'
        if self.left_on:
            return 'turn_right'
        return 'turn_left'

    def to_motor_dict(self) -> dict:
        return {
            'left':     self.left_on,
            'right':    self.right_on,
            'blinking': self.blinking,
        }


class TrashTracker:
    """
    Visual-servoing controller for trash approach.

    Usage (managed by BehaviorManager):
        tracker.begin_approach(current_path_index)
        # Each tick:
        cmd = tracker.update(trash_obs)
        if tracker.collection_triggered():
            tracker.fire_collection()
            ...
    """

    def __init__(self):
        self._cfg       = trash_cfg()
        self._active    = False
        self._start_t:  Optional[float] = None
        self._path_index_at_diversion: Optional[int] = None
        self._collected = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def begin_approach(self, path_index: int) -> None:
        """
        Signal the start of a trash approach.
        Records the path index so BehaviorManager can re-anchor after collection.
        """
        self._active    = True
        self._start_t   = time.monotonic()
        self._collected = False
        self._path_index_at_diversion = path_index
        log.info("TrashTracker: approach started", path_index=path_index)

    def abort(self) -> None:
        """Immediately abort the approach (obstacle interrupt)."""
        if self._active:
            log.info("TrashTracker: approach aborted")
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    @property
    def diversion_path_index(self) -> Optional[int]:
        """Path index at which the diversion started (for re-anchor)."""
        return self._path_index_at_diversion

    # ── Per-tick update ───────────────────────────────────────────────────────

    def update(self, trash_obs: Optional[TrashObservation]) -> MotorCmd:
        """
        Compute motor command for trash approach.

        Called every navigation tick (10 Hz).

        If trash_obs is None (lost target), robot holds heading and creeps
        forward slowly for one tick (BehaviorManager handles extended loss
        by reverting to NAVIGATE).

        Returns MotorCmd — the BehaviorManager applies this to the engine.
        """
        cfg = self._cfg

        # Timeout guard
        if self._start_t is not None:
            elapsed = time.monotonic() - self._start_t
            if elapsed > cfg.COLLECTION_TIMEOUT_S:
                log.info("TrashTracker: approach timeout", elapsed_s=round(elapsed, 1))
                self._active = False
                return self._stop_cmd()

        if trash_obs is None:
            # Target briefly lost — creep forward, maintain heading
            return MotorCmd(
                omega_cmd=0.0,
                speed_cmd=cfg.TRASH_APPROACH_SPEED * 0.3,
                left_on=True,
                right_on=True,
            )

        # ── Visual servoing ───────────────────────────────────────────────────
        # error_x: normalised pixel offset from frame centre
        #   positive = trash is RIGHT of centre → steer right (positive omega)
        #   negative = trash is LEFT of centre  → steer left  (negative omega)
        error_x = trash_obs.image_cx_norm   # already in [-1, +1]

        # Apply dead-band (suppress tiny corrections)
        deadband_norm = cfg.CENTRE_DEADBAND_PX / (320.0)  # 320 = half of 640px frame
        if abs(error_x) < deadband_norm:
            error_x = 0.0

        # Proportional steering
        # KP units: deg/s per normalised unit.  error_x=1.0 (hard right) →
        # omega = KP deg/s of right turn.
        omega_cmd = cfg.TRASH_STEERING_KP * error_x * 90.0
        # Scale by 90 to convert: KP=0.10, error=1.0 → 9 deg/s (gentle)

        # Speed: reduce proportionally with heading error (same as cruise planner)
        speed_cmd = cfg.TRASH_APPROACH_SPEED * max(0.3, 1.0 - abs(error_x))

        # Derive discrete motor states from omega (for display)
        if abs(omega_cmd) < 1.5:
            left_on, right_on = True, True      # forward
        elif omega_cmd > 0:
            left_on, right_on = True, False     # turn right
        else:
            left_on, right_on = False, True     # turn left

        return MotorCmd(
            omega_cmd=omega_cmd,
            speed_cmd=speed_cmd,
            left_on=left_on,
            right_on=right_on,
        )

    # ── Collection trigger ────────────────────────────────────────────────────

    def collection_triggered(self, trash_obs: Optional[TrashObservation]) -> bool:
        """
        Returns True when the robot is close enough to collect trash.
        Trigger: bbox area fraction >= COLLECTION_AREA_FRAC.
        """
        if trash_obs is None or not self._active:
            return False
        return trash_obs.area_frac >= self._cfg.COLLECTION_AREA_FRAC

    def fire_collection(self) -> None:
        """
        Executes the collection event.

        In real hardware: pulse a relay or servo to actuate the collection
        mechanism.  Here we log the event and set a flag.

        Integration point: replace the log.info call with your actuator code.
        """
        self._collected = True
        self._active    = False
        log.info("TrashTracker: COLLECTION TRIGGERED — actuator pulse",
                 diversion_index=self._path_index_at_diversion)
        # TODO: asyncio.create_task(actuator.pulse()) for real hardware

    @property
    def collected(self) -> bool:
        return self._collected

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _stop_cmd() -> MotorCmd:
        return MotorCmd(
            omega_cmd=0.0,
            speed_cmd=0.0,
            left_on=False,
            right_on=False,
        )

    def status_dict(self) -> dict:
        elapsed = (time.monotonic() - self._start_t) if self._start_t else 0.0
        return {
            'trash_tracker_active':   self._active,
            'trash_approach_elapsed': round(elapsed, 1),
            'trash_collected':        self._collected,
            'trash_diversion_index':  self._path_index_at_diversion,
        }
