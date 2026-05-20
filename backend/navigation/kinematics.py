"""
VesselKinematics — lightweight kinematic model for an autonomous surface vessel.

Model: unicycle / differential-drive approximation suitable for a slow ASV.
State: (lat, lng, heading_deg, speed_mps)

Physics applied each tick (dt seconds):
  1. Angular velocity is rate-limited by MAX_TURN_RATE_DPS.
  2. Heading updates by omega * dt (integrated rotation).
  3. Speed relaxes toward commanded speed via first-order lag (SPEED_TAU).
  4. Position integrates from heading + speed (forward Euler on local metric plane).

This is NOT full hydrodynamics — it is intentionally lightweight for:
  - Raspberry Pi 5 real-time execution
  - GPS + compass accuracy limits (~1–3 m, ~2° respectively)
  - Differential-drive / twin-thruster ASV geometry

Tunable parameters are all class-level constants; override per-instance
via VesselKinematics(config=VesselConfig(...)) for future PID upgrades.
"""

import math
from dataclasses import dataclass, field


@dataclass
class VesselConfig:
    # ── Speed ──────────────────────────────────────────────────────────────────
    cruise_speed_mps: float = 1.5      # normal forward speed  (m/s)
    max_speed_mps:    float = 2.5      # absolute maximum speed (m/s)
    speed_tau:        float = 2.0      # first-order speed lag  (seconds)
                                       # speed reaches 63% of target in tau seconds

    # ── Turning ────────────────────────────────────────────────────────────────
    max_turn_rate_dps: float = 15.0    # maximum angular velocity (degrees/second)
                                       # typical small ASV: 10–20 deg/s
    turn_speed_factor: float = 0.6    # fraction of cruise speed during hard turns
                                       # reduces speed when turning sharply

    # ── Pure pursuit ───────────────────────────────────────────────────────────
    lookahead_m:      float = 8.0      # pure-pursuit lookahead distance (metres)
                                       # increase for smoother but wider turns
    waypoint_radius_m: float = 4.0    # waypoint acceptance radius (metres)
                                       # vessel advances to next WP when inside this

    # ── Proportional heading controller ───────────────────────────────────────
    # Commanded angular velocity = Kp * heading_error (clamped to max_turn_rate)
    heading_kp: float = 0.4           # proportional gain (deg/s per deg of error)
                                       # lower = smoother but slower corrections


class VesselKinematics:
    """
    Integrates vessel state forward by dt seconds given a commanded angular velocity.

    Usage (each navigation tick):
        omega_cmd = controller.compute_omega(state, lookahead_point)
        state = kinematics.step(state, omega_cmd, dt)
    """

    def __init__(self, config: VesselConfig | None = None):
        self.cfg = config or VesselConfig()

        # State
        self.lat:     float = 0.0
        self.lng:     float = 0.0
        self.heading: float = 0.0   # degrees, 0 = North, clockwise
        self.speed:   float = 0.0   # m/s, current

    def reset(self, lat: float, lng: float, heading: float = 0.0):
        self.lat     = lat
        self.lng     = lng
        self.heading = heading
        self.speed   = 0.0

    def step(self, omega_cmd: float, dt: float, speed_cmd: float | None = None) -> None:
        """
        Advance vessel state by dt seconds.

        omega_cmd : commanded angular velocity in degrees/second (+ve = turn right)
        dt        : time step in seconds
        speed_cmd : commanded speed in m/s (defaults to cruise_speed_mps)
        """
        cfg = self.cfg
        if speed_cmd is None:
            speed_cmd = cfg.cruise_speed_mps

        # ── 1. Rate-limit angular velocity ────────────────────────────────────
        # The vessel cannot rotate faster than max_turn_rate_dps regardless of
        # commanded value.  This is the primary source of realistic curved turns.
        omega = max(-cfg.max_turn_rate_dps,
                    min( cfg.max_turn_rate_dps, omega_cmd))

        # ── 2. Integrate heading ──────────────────────────────────────────────
        self.heading = (self.heading + omega * dt) % 360

        # ── 3. Reduce speed during hard turns ─────────────────────────────────
        # When |omega| is near maximum, blend speed down to turn_speed_factor.
        # Linear interpolation between full speed (omega=0) and reduced speed.
        turn_fraction = min(abs(omega) / cfg.max_turn_rate_dps, 1.0)
        effective_speed_cmd = speed_cmd * (
            1.0 - turn_fraction * (1.0 - cfg.turn_speed_factor)
        )

        # ── 4. First-order speed lag ──────────────────────────────────────────
        # dv/dt = (v_cmd - v) / tau  →  Euler step
        alpha = dt / cfg.speed_tau
        self.speed += alpha * (effective_speed_cmd - self.speed)
        self.speed = max(0.0, min(self.speed, cfg.max_speed_mps))

        # ── 5. Integrate position (forward Euler in local metric plane) ────────
        # Heading convention: 0°=North, 90°=East (standard compass).
        # x = East component, y = North component.
        dist = self.speed * dt
        hdg_rad = math.radians(self.heading)
        dx_m = dist * math.sin(hdg_rad)   # East  (+East)
        dy_m = dist * math.cos(hdg_rad)   # North (+North)

        meters_per_lat = 110540.0
        meters_per_lng = 111320.0 * math.cos(math.radians(self.lat))

        self.lat += dy_m / meters_per_lat
        self.lng += dx_m / meters_per_lng

    @property
    def state_dict(self) -> dict:
        return {
            'lat':     self.lat,
            'lng':     self.lng,
            'heading': self.heading,
            'speed':   round(self.speed, 3),
        }
