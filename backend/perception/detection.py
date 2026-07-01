"""
perception/detection.py — Shared detection dataclasses.

These are the only structures that flow between:
  Hailo inference thread → Detection Queue → PerceptionManager → BehaviorManager

Kept deliberately minimal so the queue carries no unnecessary data across
the thread boundary.  All image-space arithmetic is done in PerceptionManager
or TrashTracker — not here.
"""

from dataclasses import dataclass, field
from typing import Optional
import time


@dataclass
class BBox:
    """
    Bounding box in pixel coordinates (top-left origin, same as YOLO output).

    All values are ABSOLUTE pixels (not normalised) relative to the
    inference input resolution (default 640×640).
    """
    x:      float   # left edge
    y:      float   # top edge
    w:      float   # width
    h:      float   # height

    @property
    def center_x(self) -> float:
        return self.x + self.w / 2.0

    @property
    def center_y(self) -> float:
        return self.y + self.h / 2.0

    @property
    def area(self) -> float:
        return self.w * self.h

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h


@dataclass
class RawDetection:
    """
    Single detection as produced by the Hailo inference thread.
    This is the only object put into the thread-safe detection queue.

    class_id: 0 = obstacle, 1 = trash  (matches HEF training labels)
    confidence: 0.0 – 1.0
    bbox: absolute pixel bounding box at inference resolution
    frame_w, frame_h: actual inference input size (used for normalisation)
    timestamp: monotonic seconds at capture time
    """
    class_id:   int
    confidence: float
    bbox:       BBox
    frame_w:    int   = 640
    frame_h:    int   = 640
    timestamp:  float = field(default_factory=time.monotonic)


@dataclass
class ObstacleObservation:
    """
    Processed obstacle observation output by PerceptionManager.

    image_cx_norm: obstacle centre x, normalised to [-1, +1]
                   (0 = frame centre, -1 = left edge, +1 = right edge)
    width_frac:    obstacle width / frame width (0–1)
                   used for proximity estimation and stop threshold
    confidence:    highest-confidence detection in this composite observation
    """
    image_cx_norm: float       # [-1, +1]
    width_frac:    float       # [0, 1]
    confidence:    float
    timestamp:     float = field(default_factory=time.monotonic)


@dataclass
class TrashObservation:
    """
    Processed trash observation output by PerceptionManager.

    image_cx_norm: trash centre x, normalised to [-1, +1]
    image_cy_norm: trash centre y, normalised to [-1, +1]
                   (0 = centre, -1 = top edge, +1 = bottom edge)
    area_frac:     bbox_area / (frame_w * frame_h) — used for collection trigger
    confidence:    detection confidence
    """
    image_cx_norm: float       # [-1, +1]
    image_cy_norm: float       # [-1, +1]
    area_frac:     float       # [0, 1]
    confidence:    float
    timestamp:     float = field(default_factory=time.monotonic)
