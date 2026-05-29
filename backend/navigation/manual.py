"""
ManualController — holds and applies manual motor commands.

Differential-drive model:
  Both ON  → forward  (omega=0,  speed=throttle * cruise)
  Left OFF → turn right (omega=+max_turn_rate * throttle, speed=reduced)
  Right OFF → turn left (omega=-max_turn_rate * throttle, speed=reduced)
  Both OFF → stop

This is intentionally simple — the real hardware integration point is
_compute_motor_outputs(), which future code can replace with actual PWM signals.
"""

from dataclasses import dataclass, field
from navigation.kinematics import VesselConfig


@dataclass
class ManualState:
    left:     bool  = False
    right:    bool  = False
    throttle: float = 0.5    # 0.0 – 1.0

    @property
    def any_active(self) -> bool:
        return self.left or self.right


class ManualController:
    def __init__(self, config: VesselConfig):
        self.cfg   = config
        self.state = ManualState()

    def set_left(self, on: bool)      -> None: self.state.left     = on
    def set_right(self, on: bool)     -> None: self.state.right    = on
    def set_throttle(self, t: float)  -> None: self.state.throttle = max(0.0, min(1.0, t))

    def stop(self) -> None:
        self.state.left     = False
        self.state.right    = False

    def compute(self) -> tuple[float, float]:
        """
        Returns (omega_cmd_dps, speed_cmd_mps) from current manual state.

        Used only in mock simulation mode — on real hardware the motor
        driver receives the boolean left/right signals directly.
        """
        s   = self.state
        cfg = self.cfg

        if s.left and s.right:
            # Both on → straight ahead
            return 0.0, cfg.cruise_speed_mps * s.throttle

        if s.left and not s.right:
            # Left on, right off → turn right
            omega = cfg.max_turn_rate_dps * s.throttle
            speed = cfg.cruise_speed_mps * s.throttle * cfg.turn_speed_factor
            return omega, speed

        if s.right and not s.left:
            # Right on, left off → turn left
            omega = -cfg.max_turn_rate_dps * s.throttle
            speed = cfg.cruise_speed_mps * s.throttle * cfg.turn_speed_factor
            return omega, speed

        # Both off → stop
        return 0.0, 0.0

    def to_motor_dict(self) -> dict:
        s = self.state
        return {
            'left':     s.left,
            'right':    s.right,
            'blinking': False,
        }

    def to_steering_label(self) -> str:
        s = self.state
        if not s.left and not s.right:  return 'stop'
        if s.left  and s.right:         return 'forward'
        if s.left  and not s.right:     return 'turn_right'
        return 'turn_left'
