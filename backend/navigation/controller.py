"""
PurePursuitController — computes commanded angular velocity toward a lookahead point.

Pure Pursuit is a path-tracking algorithm originally developed for autonomous ground
vehicles (Coulter, 1992, CMU).  It works as follows:

  1. Find the "lookahead point": the first point on the path that is exactly
     lookahead_distance metres ahead of the vessel.  If no such point exists
     (vessel is near the end of the path), use the final path point.

  2. Compute the bearing from vessel to lookahead point.

  3. Compute heading error (signed, -180 to +180).

  4. Map heading error → angular velocity command via proportional control:
         omega_cmd = Kp * heading_error
     Clamped to max_turn_rate by VesselKinematics.step().

Why Pure Pursuit instead of point-to-point bearing?
  - The vessel aims at a moving target AHEAD of it, not the next static waypoint.
  - As the vessel approaches a corner, the lookahead point smoothly rounds the bend.
  - Larger lookahead_m → wider, smoother turns.
  - Smaller lookahead_m → tighter tracking, possible oscillation.
  - For lawnmower sweeps the same controller works without modification.

Limitations (acceptable for this application):
  - Does not account for cross-track error explicitly (Stanley controller does this,
    but requires higher-rate GPS and is overkill for 1–3 m GPS accuracy).
  - Does not model water current drift (add feedforward term in future).
  - Constant lookahead distance (adaptive lookahead based on speed is a v2 upgrade).
"""

import math
from utils.geo import haversine, bearing as calc_bearing, heading_error as calc_error
from navigation.kinematics import VesselConfig


class PurePursuitController:
    """
    Stateless controller: given vessel state and path, returns omega_cmd (deg/s).

    All tuning lives in VesselConfig so the engine and controller share one
    config object.
    """

    def __init__(self, config: VesselConfig | None = None):
        self.cfg = config or VesselConfig()

    # ── Lookahead point selection ─────────────────────────────────────────────

    def find_lookahead(
        self,
        vessel_lat: float,
        vessel_lng: float,
        path_points: list,          # list of PathPoint
        current_index: int,
    ) -> tuple:
        """
        Find the lookahead point on the path.

        Algorithm: parametric segment–circle intersection (Coulter 1992).

        For each segment [A, B] starting at current_index, we solve for t ∈ [0,1]
        such that |A + t*(B-A)|² = ld² (circle centred at vessel, radius=lookahead_m).

        Expanding:
            |d|²·t² + 2(A·d)·t + (|A|²-ld²) = 0
        where d = B - A, A and B are in vessel-centred local metres.

        This form is numerically stable for all segment orientations (no division
        by dx or dy, so horizontal/vertical segments work correctly).

        We want the largest valid t (furthest intersection along the segment),
        which corresponds to the intersection in the forward direction of travel.

        Returns (lookahead_lat, lookahead_lng, lookahead_index) where
        lookahead_index is the index of the endpoint of the segment that contains
        the lookahead point — used by the sequencer to advance current_index.
        """
        ld  = self.cfg.lookahead_m
        ld2 = ld * ld
        n   = len(path_points)

        if current_index >= n:
            last = path_points[-1]
            return last.lat, last.lng, n - 1

        mplat = 110540.0
        mplng = 111320.0 * math.cos(math.radians(vessel_lat))

        # Walk segments forward from current_index, take the FIRST (earliest)
        # segment whose forward intersection we can find.
        for i in range(current_index, min(n - 1, current_index + 50)):
            a = path_points[i]
            b = path_points[i + 1]

            # Convert A and B to vessel-centred local metres
            ax = (a.lng - vessel_lng) * mplng
            ay = (a.lat - vessel_lat) * mplat
            bx = (b.lng - vessel_lng) * mplng
            by = (b.lat - vessel_lat) * mplat

            dx = bx - ax
            dy = by - ay

            # Quadratic coefficients:  (dr2)t² + (2·f_dot_d)t + (f·f - ld²) = 0
            # where f = A (the vector from vessel to segment start)
            dr2   = dx*dx + dy*dy
            if dr2 < 1e-10:
                continue           # degenerate zero-length segment

            f_dot_d = ax*dx + ay*dy
            f_sq    = ax*ax + ay*ay

            a_coef =  dr2
            b_coef =  2.0 * f_dot_d
            c_coef =  f_sq - ld2

            discriminant = b_coef*b_coef - 4.0*a_coef*c_coef
            if discriminant < 0:
                continue           # segment entirely outside lookahead circle

            sqrt_disc = math.sqrt(discriminant)

            # Two candidate t values; t2 ≥ t1 always.
            # We prefer t2 (forward intersection) then t1 (entry intersection).
            t2 = (-b_coef + sqrt_disc) / (2.0 * a_coef)
            t1 = (-b_coef - sqrt_disc) / (2.0 * a_coef)

            # Pick the largest t that is still within [0, 1]
            t = None
            if 0.0 <= t2 <= 1.0:
                t = t2
            elif 0.0 <= t1 <= 1.0:
                t = t1

            if t is None:
                # Circle intersects the infinite line but not this segment.
                # If the vessel is INSIDE the circle for the whole segment
                # (both endpoints closer than ld), continue to next segment.
                continue

            # Unproject intersection point back to LatLng
            ix_m = ax + t * dx
            iy_m = ay + t * dy
            la_lat = vessel_lat + iy_m / mplat
            la_lng = vessel_lng + ix_m / mplng

            # lookahead_index = index of segment endpoint B, so the sequencer
            # knows waypoints up to (but not including) i+1 are behind us.
            return la_lat, la_lng, i + 1

        # ── Fallback ──────────────────────────────────────────────────────────
        # No segment intersection found — the vessel is within the last lookahead
        # distance of the path end, or path is very short.
        # Aim directly at the nearest upcoming path point.
        target = path_points[min(current_index, n - 1)]
        return target.lat, target.lng, current_index

    # ── Omega command ─────────────────────────────────────────────────────────

    def compute_omega(
        self,
        vessel_lat: float,
        vessel_lng: float,
        vessel_heading: float,
        lookahead_lat: float,
        lookahead_lng: float,
    ) -> float:
        """
        Proportional heading controller.

        omega_cmd = Kp * heading_error

        The sign convention:
          positive omega → turn right (clockwise heading increase)
          negative omega → turn left

        This will be clamped to ±max_turn_rate_dps inside VesselKinematics.step().
        """
        req = calc_bearing(vessel_lat, vessel_lng, lookahead_lat, lookahead_lng)
        err = calc_error(vessel_heading, req)
        omega = self.cfg.heading_kp * err
        return omega   # degrees/second

    # ── Speed command ─────────────────────────────────────────────────────────

    def compute_speed(self, heading_error_deg: float) -> float:
        """
        Reduce speed proportionally when heading error is large.
        This prevents the vessel from overshooting corners at high speed.

        speed = cruise * max(min_factor, 1 - |error|/90)
        """
        min_factor = self.cfg.turn_speed_factor
        factor = max(min_factor, 1.0 - abs(heading_error_deg) / 90.0)
        return self.cfg.cruise_speed_mps * factor

    # ── Steering command for UI display ──────────────────────────────────────

    @staticmethod
    def steering_from_omega(omega: float, max_rate: float) -> str:
        """Map angular velocity to a discrete steering label for the frontend UI."""
        ratio = omega / max_rate if max_rate > 0 else 0
        if abs(ratio) < 0.15:
            return 'forward'
        # Merged duplicate branch: original had separate < 0.55 and < 0.90
        # conditions returning identical values.
        if abs(ratio) < 0.90:
            return 'turn_right' if omega > 0 else 'turn_left'
        return 'large_correction'

    @staticmethod
    def motor_from_steering(steering: str, omega: float, max_rate: float) -> dict:
        """
        Map steering command to left/right motor booleans.

        For a differential-drive ASV:
          forward      → both ON
          turn_right   → left ON, right reduced/OFF
          turn_left    → right ON, left reduced/OFF
          large_corr   → alternate blinking (strong turn signal)
        """
        blinking = steering == 'large_correction'
        if steering == 'forward':
            return {'left': True,  'right': True,  'blinking': False}
        if steering == 'turn_right':
            return {'left': True,  'right': False, 'blinking': False}
        if steering == 'turn_left':
            return {'left': False, 'right': True,  'blinking': False}
        # large_correction
        return {'left': False, 'right': False, 'blinking': True}
