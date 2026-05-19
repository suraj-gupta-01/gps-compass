from typing import List, Optional, Tuple, Dict, Any
from .models import MissionJSON, WaypointPathSegment, AreaCoverageSegment
from coverage.lawnmower import generate_lawnmower
from utils.geo import haversine, bearing, heading_error

# Label each point with which segment it came from
class PathPoint:
    __slots__ = ('lat','lng','segment_label','segment_type')
    def __init__(self, lat, lng, label, seg_type):
        self.lat = lat
        self.lng = lng
        self.segment_label = label
        self.segment_type = seg_type

    def to_dict(self):
        return {'lat': self.lat, 'lng': self.lng}

class MissionSequencer:
    """
    Flattens a PlannerMissionJSON into an ordered list of PathPoints.
    Handles waypoint_path and area_coverage segments.
    Tracks current position in the path during execution.
    """

    ARRIVAL_RADIUS_M = 3.0   # metres — consider waypoint reached

    def __init__(self):
        self.path: List[PathPoint] = []
        self.current_index: int = 0
        self.loaded: bool = False
        self.mission: Optional[MissionJSON] = None

    def load(self, mission: MissionJSON) -> List[Dict]:
        """Build flat path from mission JSON. Returns generated path as list of dicts."""
        self.path = []
        self.current_index = 0
        self.loaded = bool(mission.groundControl)
        self.mission = mission

        gc = mission.groundControl
        if not gc:
            return []

        def push(lat, lng, label, seg_type):
            self.path.append(PathPoint(lat, lng, label, seg_type))

        # Start from GCS
        push(gc.position.lat, gc.position.lng, 'Ground Control', 'gcs')

        for seg in mission.mission:
            if isinstance(seg, WaypointPathSegment):
                for pt in seg.points:
                    push(pt.lat, pt.lng, seg.label, 'waypoint_path')

            elif isinstance(seg, AreaCoverageSegment):
                polygon_raw = [{'lat': v.lat, 'lng': v.lng} for v in seg.polygon]
                sweep_pts = generate_lawnmower(polygon_raw, seg.sweepWidth)
                for sp in sweep_pts:
                    push(sp['lat'], sp['lng'], seg.label, 'area_coverage')

        # Return to GCS
        push(gc.position.lat, gc.position.lng, 'Return to GCS', 'gcs')

        return [p.to_dict() for p in self.path]

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

    def update(self, lat: float, lng: float) -> Optional[PathPoint]:
        """
        Called each telemetry tick with current vessel position.
        Advances index when vessel reaches current target.
        Returns the current target point.
        """
        if self.is_complete:
            return None
        target = self.current_target
        dist = haversine(lat, lng, target.lat, target.lng)
        if dist <= self.ARRIVAL_RADIUS_M:
            self.current_index += 1
        return self.current_target

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