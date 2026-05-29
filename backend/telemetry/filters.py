"""
Sensor filters for GPS and compass telemetry.

Two problems on real hardware:

1. Compass jitter — cheap magnetometers near motors produce ±5-15° noise.
   Without filtering, Kp * noise feeds directly into the controller as a
   turn command.  An EMA with alpha=0.3 removes ~70% of high-freq jitter
   while adding only ~3 ticks (0.3s) of lag — acceptable for a slow ASV.

2. GPS outliers — GPS occasionally produces a single wildly wrong fix
   (multipath, satellite reacquisition) before returning to normal.
   Reject any fix that jumps more than MAX_GPS_JUMP_M in one tick.
   Hold the previous valid fix instead — far safer than navigating to
   a position 200m away for one tick.

Both filters are stateful, cheap, and require no external libraries.
"""

import math


class CompassFilter:
    """
    Exponential Moving Average (EMA) for compass heading.

    Handles the 359→0 wraparound correctly by filtering on the unit-circle
    components (sin, cos) rather than the angle directly, then converting back.

    alpha=1.0 → no filtering (raw values)
    alpha=0.1 → heavy smoothing (slow response)
    alpha=0.3 → good balance for 10 Hz navigation at 1-2 m/s ASV
    """

    def __init__(self, alpha: float = 0.3):
        if not 0.0 < alpha <= 1.0:
            raise ValueError(f"alpha must be in (0, 1], got {alpha}")
        self.alpha = alpha
        self._sin: float | None = None
        self._cos: float | None = None

    def update(self, heading_deg: float) -> float:
        rad = math.radians(heading_deg % 360)
        s = math.sin(rad)
        c = math.cos(rad)

        if self._sin is None:
            # First reading — initialise with no lag
            self._sin = s
            self._cos = c
        else:
            self._sin = self.alpha * s + (1 - self.alpha) * self._sin
            self._cos = self.alpha * c + (1 - self.alpha) * self._cos

        return (math.degrees(math.atan2(self._sin, self._cos)) + 360) % 360

    def reset(self):
        self._sin = None
        self._cos = None


class GpsFilter:
    """
    GPS outlier rejection via maximum-jump threshold.

    If a new fix is more than MAX_JUMP_M metres from the previous valid fix,
    it is rejected as a probable outlier and the previous fix is held.
    Up to MAX_CONSECUTIVE_REJECTS consecutive rejects are allowed before
    accepting the new fix unconditionally (handles genuine large movements
    after a GPS outage / reacquisition delay).

    Also validates that lat/lng are in plausible geographic ranges.
    """

    MAX_JUMP_M            = 25.0   # metres — max plausible movement per tick
    MAX_CONSECUTIVE_REJECTS = 5    # after this many rejects, accept next fix

    def __init__(self, max_jump_m: float = MAX_JUMP_M):
        self.max_jump_m = max_jump_m
        self._lat: float | None = None
        self._lng: float | None = None
        self._rejects: int = 0

    @staticmethod
    def _is_valid_fix(lat: float, lng: float) -> bool:
        """Basic sanity check — rejects NaN, inf, and out-of-range coordinates."""
        if not math.isfinite(lat) or not math.isfinite(lng):
            return False
        if not (-90.0 <= lat <= 90.0):
            return False
        if not (-180.0 <= lng <= 180.0):
            return False
        return True

    def _haversine(self, lat1, lng1, lat2, lng2) -> float:
        R = 6371000.0
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp = math.radians(lat2 - lat1)
        dl = math.radians(lng2 - lng1)
        a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))

    def update(self, lat: float, lng: float) -> tuple[float, float, bool]:
        """
        Returns (filtered_lat, filtered_lng, accepted).
        accepted=False means the fix was rejected and previous values are held.
        """
        if not self._is_valid_fix(lat, lng):
            if self._lat is not None:
                return self._lat, self._lng, False
            # No previous fix either — hold at 0,0 (caller should handle)
            return 0.0, 0.0, False

        if self._lat is None:
            # First ever fix — accept unconditionally
            self._lat, self._lng = lat, lng
            self._rejects = 0
            return lat, lng, True

        dist = self._haversine(self._lat, self._lng, lat, lng)

        if dist <= self.max_jump_m or self._rejects >= self.MAX_CONSECUTIVE_REJECTS:
            # Accept: within jump threshold, or we've held old fix too long
            self._lat, self._lng = lat, lng
            self._rejects = 0
            return lat, lng, True
        else:
            # Reject: return held position
            self._rejects += 1
            return self._lat, self._lng, False

    def reset(self):
        self._lat = None
        self._lng = None
        self._rejects = 0
