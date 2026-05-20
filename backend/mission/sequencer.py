"""
MissionSequencer — Pure-Pursuit aware waypoint sequencing.

Key change from v1:
  Waypoint advancement is now driven by the LOOKAHEAD INDEX returned by the
  PurePursuitController, not by a hard arrival-radius circle around the exact
  waypoint coordinate.

  This means:
    - The vessel begins curving BEFORE reaching the waypoint (the lookahead
      point rounds the corner ahead of the vessel).
    - Waypoint acceptance still has a configurable radius, but it is only
      the fallback — the lookahead normally carries the vessel past each point
      without stopping or snapping.
    - Coverage sweep points use a tighter acceptance radius since the lawnmower
      lines require precise tracking.
"""

from typing import List, Optional, Dict
from .models import MissionJSON, WaypointPathSegment, AreaCoverageSegment
from coverage.lawnmower import generate_lawnmower
from utils.geo import haversine


class PathPoint:
    __slots__ = ('lat', 'lng', 'segment_label', 'segment_type', 'acceptance_radius_m')

    def __init__(self, lat: float, lng: float, label: str, seg_type: str,
                 acceptance_radius_m: float = 4.0):
        self.lat = lat
        self.lng = lng
        self.segment_label = label
        self.segment_type = seg_type
        self.acceptance_radius_m = acceptance_radius_m

    def to_dict(self) -> Dict:
        return {'lat': self.lat, 'lng': self.lng}


class MissionSequencer:
    """
    Flattens mission JSON into an ordered PathPoint list.
    Tracks the active path index during execution.

    Waypoint advancement rules (in priority order):
      1. If the lookahead controller has moved its lookahead index past
         current_index (advance_to_index call) → advance.
      2. If vessel is within acceptance_radius_m of current point → advance.

    Rule 1 handles the normal smooth-turn case.
    Rule 2 is the safety fallback for slow speeds / tight paths where the
    lookahead circle never crosses the next segment.
    """

    # Per-segment-type acceptance radii
    RADIUS_GCS_M      = 3.0   # tight: must actually reach home base
    RADIUS_WAYPOINT_M = 5.0   # relaxed: pure-pursuit normally handles this
    RADIUS_COVERAGE_M = 2.5   # tighter: sweep rows need accurate execution

    def __init__(self):
        self.path: List[PathPoint] = []
        self.current_index: int = 0
        self.loaded: bool = False
        self.mission: Optional[MissionJSON] = None

    # ── Load ─────────────────────────────────────────────────────────────────

    def load(self, mission: MissionJSON) -> List[Dict]:
        self.path = []
        self.current_index = 0
        self.loaded = bool(mission.groundControl)
        self.mission = mission

        gc = mission.groundControl
        if not gc:
            return []

        def push(lat, lng, label, seg_type, radius):
            self.path.append(PathPoint(lat, lng, label, seg_type, radius))

        push(gc.position.lat, gc.position.lng,
             'Ground Control', 'gcs', self.RADIUS_GCS_M)

        for seg in mission.mission:
            if isinstance(seg, WaypointPathSegment):
                for pt in seg.points:
                    push(pt.lat, pt.lng, seg.label, 'waypoint_path',
                         self.RADIUS_WAYPOINT_M)

            elif isinstance(seg, AreaCoverageSegment):
                polygon_raw = [{'lat': v.lat, 'lng': v.lng} for v in seg.polygon]
                sweep_pts = generate_lawnmower(polygon_raw, seg.sweepWidth)
                for sp in sweep_pts:
                    push(sp['lat'], sp['lng'], seg.label, 'area_coverage',
                         self.RADIUS_COVERAGE_M)

        push(gc.position.lat, gc.position.lng,
             'Return to GCS', 'gcs', self.RADIUS_GCS_M)

        return [p.to_dict() for p in self.path]

    # ── State ─────────────────────────────────────────────────────────────────

    def reset(self):
        self.current_index = 0

    @property
    def total(self) -> int:
        return len(self.path)

    @property
    def is_complete(self) -> bool:
        return self.current_index >= self.total

    @property
    def current_target(self) -> Optional[PathPoint]:
        if self.is_complete:
            return None
        return self.path[self.current_index]

    @property
    def progress(self) -> float:
        if self.total == 0:
            return 0.0
        return min(self.current_index / self.total, 1.0)

    # ── Advancement ───────────────────────────────────────────────────────────

    def advance_to_index(self, lookahead_idx: int):
        """
        Called by the navigation engine when the pure-pursuit lookahead has
        moved past one or more waypoints.  Safely advances current_index.
        """
        if lookahead_idx > self.current_index:
            self.current_index = min(lookahead_idx, self.total)

    def update(self, lat: float, lng: float) -> Optional[PathPoint]:
        """
        Fallback advancement: advance current_index if vessel is within
        acceptance_radius of the current target.  This handles edge cases
        where the lookahead circle never crosses the next segment (very short
        segments, near end-of-path, sharp 180° turns in coverage sweeps).
        """
        if self.is_complete:
            return None
        target = self.current_target
        dist = haversine(lat, lng, target.lat, target.lng)
        if dist <= target.acceptance_radius_m:
            self.current_index = min(self.current_index + 1, self.total)
        return self.current_target

    # ── Nav state ─────────────────────────────────────────────────────────────

    def nav_state(self) -> str:
        if not self.loaded or self.total == 0:
            return 'idle'
        if self.is_complete:
            return 'completed'
        target = self.current_target
        if target is None:
            return 'completed'
        if target.segment_type == 'gcs' and self.current_index > 0:
            return 'returning'
        if target.segment_type == 'area_coverage':
            return 'coverage'
        return 'navigating'
