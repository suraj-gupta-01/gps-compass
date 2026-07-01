"""
perception/perception_manager.py — Unified perception layer.

Role in the pipeline
────────────────────
  HailoRunner (thread) ──► queue ──► PerceptionManager ──► BehaviorManager

PerceptionManager bridges the Hailo inference thread and the asyncio
navigation world.  Each call to update() drains the detection queue and
returns the current best ObstacleObservation and TrashObservation (or None
if none are confirmed).

Design goals
────────────
  - Zero blocking I/O (all queue access is non-blocking get_nowait)
  - Temporal filtering: detections must appear for MIN_CONFIRM_FRAMES
    consecutive ticks before they are promoted to confirmed observations.
    This eliminates single-frame phantom detections.
  - Decay: confirmed observations that stop arriving are held for
    MAX_MISS_FRAMES ticks, then cleared.  This handles short occlusions.
  - Best-target selection: among multiple candidates of the same class,
    select the one with the highest confidence × area product.
  - No state shared with the Hailo thread beyond the queue.

Observation lifetime state machine (per tracked slot):

    ABSENT ──[seen once]──► CANDIDATE ──[seen N frames]──► CONFIRMED
       ▲                         │                              │
       └─[miss > MAX_MISS]───────┘◄─────────[miss frames]──────┘
"""

import queue
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, List

from config import perception_cfg, PerceptionConfig
from perception.detection import RawDetection, ObstacleObservation, TrashObservation
from utils.logger import log


# ── Internal tracking slot ────────────────────────────────────────────────────

@dataclass
class _TrackSlot:
    """Single-class detection tracker with confirm/decay logic."""
    class_id:      int
    confirm_count: int   = 0
    miss_count:    int   = 0
    confirmed:     bool  = False

    # Best detection in this tick (refreshed each update call)
    best_det: Optional[RawDetection] = None

    def feed(self, det: RawDetection, min_confirm: int) -> None:
        """Called when a detection for this slot arrives this tick."""
        self.miss_count = 0
        # Replace best_det if this one scores higher (confidence × area)
        if self.best_det is None or \
           (det.confidence * det.bbox.area >
            self.best_det.confidence * self.best_det.bbox.area):
            self.best_det = det

        self.confirm_count += 1
        if self.confirm_count >= min_confirm:
            self.confirmed = True

    def decay(self, max_miss: int) -> None:
        """Called when NO matching detection arrived this tick."""
        self.miss_count   += 1
        self.confirm_count = max(0, self.confirm_count - 1)
        if self.miss_count > max_miss:
            self.confirmed     = False
            self.confirm_count = 0
        self.best_det = None   # stale; cleared each tick regardless

    def reset_tick(self):
        """Clear per-tick transient state before processing new detections."""
        self.best_det = None


# ── Perception Manager ────────────────────────────────────────────────────────

class PerceptionManager:
    """
    Drains the detection queue and maintains confirmed obstacle / trash state.

    Call update() once per navigation tick.
    Then read obstacle and trash properties.

    Thread safety: update() is called from the asyncio navigation loop.
    The only cross-thread interaction is the queue (thread-safe by design).
    """

    def __init__(self, detection_queue: queue.Queue):
        self._q    = detection_queue
        self._cfg  = perception_cfg()

        # One tracking slot per class (we have two: obstacle and trash)
        self._obstacle_slot = _TrackSlot(class_id=self._cfg.CLASS_OBSTACLE)
        self._trash_slot    = _TrackSlot(class_id=self._cfg.CLASS_TRASH)

        # Published observations (updated each tick by update())
        self._obstacle_obs: Optional[ObstacleObservation] = None
        self._trash_obs:    Optional[TrashObservation]    = None

        # Diagnostic counters
        self._total_dets_processed: int = 0
        self._total_obstacles:      int = 0
        self._total_trash:          int = 0

        # Mock injection persistence flags
        self._mock_obstacle_active: bool = False
        self._mock_trash_active:    bool = False

    # ── Main update (called every navigation tick, ~10 Hz) ───────────────────

    def update(self) -> None:
        """
        Drain detection queue and update confirmed observations.

        This is the ONLY method that should be called from the navigation loop.
        It is synchronous (no await) — all queue operations are non-blocking.
        """
        pcfg = self._cfg

        # 1. Reset per-tick transients
        self._obstacle_slot.reset_tick()
        self._trash_slot.reset_tick()

        # 2. Drain queue — process at most QUEUE_MAXSIZE * 2 items per tick
        #    to avoid spending excessive CPU when queue was accumulating.
        max_drain = self._q.maxsize * 2 if self._q.maxsize > 0 else 20
        drained   = 0

        while drained < max_drain:
            try:
                det: RawDetection = self._q.get_nowait()
            except queue.Empty:
                break

            drained += 1
            self._total_dets_processed += 1

            # Confidence gate (second line of defence; HailoRunner already gates)
            if det.class_id == pcfg.CLASS_OBSTACLE:
                if det.confidence < pcfg.OBSTACLE_CONFIDENCE_THRESHOLD:
                    continue
                if det.bbox.area < pcfg.MIN_BBOX_AREA_PX2:
                    continue
                self._obstacle_slot.feed(det, pcfg.MIN_CONFIRM_FRAMES)
                self._total_obstacles += 1

            elif det.class_id == pcfg.CLASS_TRASH:
                if det.confidence < pcfg.TRASH_CONFIDENCE_THRESHOLD:
                    continue
                if det.bbox.area < pcfg.MIN_BBOX_AREA_PX2:
                    continue
                self._trash_slot.feed(det, pcfg.MIN_CONFIRM_FRAMES)
                self._total_trash += 1

        # 3. Decay slots that received no detections this tick
        if self._obstacle_slot.best_det is None:
            self._obstacle_slot.decay(pcfg.MAX_MISS_FRAMES)
        if self._trash_slot.best_det is None:
            self._trash_slot.decay(pcfg.MAX_MISS_FRAMES)

        # 4. Build confirmed observations (or clear them)
        if not self._mock_obstacle_active:
            self._obstacle_obs = self._build_obstacle_obs()
        if not self._mock_trash_active:
            self._trash_obs = self._build_trash_obs()

        if pcfg.DEBUG_LOG_DETS and drained > 0:
            log.debug("PerceptionManager tick",
                      drained=drained,
                      obstacle_confirmed=self._obstacle_slot.confirmed,
                      trash_confirmed=self._trash_slot.confirmed)

    # ── Observation builders ──────────────────────────────────────────────────

    def _build_obstacle_obs(self) -> Optional[ObstacleObservation]:
        slot = self._obstacle_slot
        if not slot.confirmed or slot.best_det is None:
            return None

        det  = slot.best_det
        fw   = det.frame_w
        fh   = det.frame_h

        cx_norm = (det.bbox.center_x - fw / 2.0) / (fw / 2.0)  # [-1, +1]
        w_frac  = det.bbox.w / fw

        return ObstacleObservation(
            image_cx_norm=cx_norm,
            width_frac=w_frac,
            confidence=det.confidence,
        )

    def _build_trash_obs(self) -> Optional[TrashObservation]:
        slot = self._trash_slot
        if not slot.confirmed or slot.best_det is None:
            return None

        det = slot.best_det
        fw  = det.frame_w
        fh  = det.frame_h

        cx_norm   = (det.bbox.center_x - fw / 2.0) / (fw / 2.0)
        cy_norm   = (det.bbox.center_y - fh / 2.0) / (fh / 2.0)
        area_frac = det.bbox.area / (fw * fh)

        return TrashObservation(
            image_cx_norm=cx_norm,
            image_cy_norm=cy_norm,
            area_frac=area_frac,
            confidence=det.confidence,
        )

    # ── Published outputs ─────────────────────────────────────────────────────

    @property
    def obstacle(self) -> Optional[ObstacleObservation]:
        """Current confirmed obstacle observation, or None."""
        return self._obstacle_obs

    @property
    def trash(self) -> Optional[TrashObservation]:
        """Current confirmed trash observation, or None."""
        return self._trash_obs

    @property
    def obstacle_active(self) -> bool:
        return self._obstacle_obs is not None

    @property
    def trash_active(self) -> bool:
        return self._trash_obs is not None

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def status_dict(self) -> dict:
        obs_slot = self._obstacle_slot
        trsh_slot = self._trash_slot
        return {
            'perception_obstacle_confirmed': obs_slot.confirmed,
            'perception_trash_confirmed':    trsh_slot.confirmed,
            'obstacle_confidence':           round(self._obstacle_obs.confidence, 3)
                                             if self._obstacle_obs else 0.0,
            'trash_confidence':              round(self._trash_obs.confidence, 3)
                                             if self._trash_obs else 0.0,
            'trash_area_frac':               round(self._trash_obs.area_frac, 4)
                                             if self._trash_obs else 0.0,
            'total_dets_processed':          self._total_dets_processed,
        }

    def inject_mock_obstacle(self, cx_norm: float = 0.0, width_frac: float = 0.25):
        """
        Directly inject a mock obstacle observation (bypasses queue).
        For unit tests and dashboard debug panel.
        """
        self._obstacle_slot.confirmed = True
        self._obstacle_slot.miss_count = 0
        self._obstacle_obs = ObstacleObservation(
            image_cx_norm=cx_norm,
            width_frac=width_frac,
            confidence=0.85,
        )
        self._mock_obstacle_active = True

    def inject_mock_trash(self, cx_norm: float = 0.0, area_frac: float = 0.05):
        """
        Directly inject a mock trash observation (bypasses queue).
        For unit tests and dashboard debug panel.
        """
        self._trash_slot.confirmed = True
        self._trash_slot.miss_count = 0
        self._trash_obs = TrashObservation(
            image_cx_norm=cx_norm,
            image_cy_norm=0.0,
            area_frac=area_frac,
            confidence=0.80,
        )
        self._mock_trash_active = True

    def clear_mock(self):
        """Clear any injected mock observations."""
        self._obstacle_slot.confirmed = False
        self._trash_slot.confirmed    = False
        self._obstacle_obs = None
        self._trash_obs    = None
        self._mock_obstacle_active = False
        self._mock_trash_active    = False
