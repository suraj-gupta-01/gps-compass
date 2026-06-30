"""
MotorCommandWriter — sends motor commands to the Arduino over UART.

Protocol
────────
The Pi sends one ASCII line per navigation tick (10 Hz) over the Pi's
GPIO UART (/dev/ttyAMA0).  The Arduino parses the line and drives the
motor ESCs/drivers.

Frame format (PWM microseconds — standard RC servo protocol):
    CMD,<left_us>,<right_us>\n

    left_us  : integer 1000–2000  (1000=full reverse, 1500=stop, 2000=full forward)
    right_us : integer 1000–2000

Examples:
    CMD,2000,2000\n   →  full forward both motors
    CMD,2000,1500\n   →  left full, right stopped  → turns right
    CMD,1500,2000\n   →  left stopped, right full  → turns left
    CMD,1500,1500\n   →  both stopped
    CMD,1000,1000\n   →  full reverse (emergency)

Why PWM microseconds?
  - Direct mapping to Arduino PWM output values
  - Same format produced by RC receivers → Arduino sketch needs zero changes
    if you later add RC override passthrough at the Arduino level
  - Standard across ESCs, brushed motor drivers, and servo controllers
  - Human-readable and easy to debug with a serial monitor

Hardware wiring
───────────────
  Pi UART TX (GPIO 14 on UART0, /dev/ttyAMA0)
      └──────────────────────► Arduino UART RX
  Pi UART RX (GPIO 15 on UART0)
      ◄─────────────────────── Arduino UART TX (optional, for status)
  Pi GND ──────────────────── Arduino GND

  The Pi's single GPIO UART (/dev/ttyAMA0) is now dedicated to the motor link.
  Telemetry (GPS/compass) arrives via a separate USB-RS232 adapter — see
  backend/telemetry/README_RS232.md.

Mapping from navigation outputs to PWM
───────────────────────────────────────
AUTO mode:
  omega_cmd (°/s) and speed_cmd (m/s) are converted to per-motor PWM:

  net = speed_cmd / max_speed                      (0.0–1.0 normalised)
  turn = omega_cmd / max_turn_rate                 (−1.0 to +1.0)
  left_pwr  = clamp(net + turn, −1.0, 1.0)
  right_pwr = clamp(net − turn, −1.0, 1.0)
  left_us   = 1500 + left_pwr  × 500              (1000–2000)
  right_us  = 1500 + right_pwr × 500

  This is the standard differential-drive mixing formula.  It gives:
    - Straight ahead: both 2000 at full speed
    - Gentle right turn: left faster, right slower
    - Hard right:  left 2000, right 1500 (or less)
    - Pivot turn:  left 2000, right 1000

MANUAL mode (software override from dashboard):
  left_us  = 2000 if left else 1500
  right_us = 2000 if right else 1500
  Throttle scales the deviation from 1500.

STOP / IDLE:
  CMD,1500,1500\n

Arduino expected behaviour
───────────────────────────
The Arduino should:
  1. Parse the CMD line with Serial.parseInt():
        left  = Serial.parseInt();
        right = Serial.parseInt();
  2. Validate 1000 ≤ left,right ≤ 2000
  3. Apply to motor driver/ESC via standard Arduino PWM output (analogWrite)
  4. Implement a watchdog: if no CMD received for >300ms → set both to 1500 (stop)
     This is critical — if the Pi crashes or UART disconnects, the Arduino must
     not continue executing the last command.

Failsafe watchdog note
──────────────────────
The Arduino-side watchdog is NOT implemented here — it must be in the Arduino sketch.
This is by design: the Pi cannot guarantee the Arduino received a stop command if
the Pi itself crashes.  The Arduino must independently time out and stop.
"""

import os
import asyncio
from utils.logger import log

# ── Configuration ──────────────────────────────────────────────────────────────

MOTOR_UART_PORT  = '/dev/ttyAMA0'   # Pi TX → Arduino RX
MOTOR_UART_BAUD  = 115200
PWM_STOP         = 1500             # microseconds — neutral/stop
PWM_MIN          = 1000             # full reverse
PWM_MAX          = 2000             # full forward
MOCK_MODE        = os.environ.get('AERONAV_MOTOR_MOCK', '1') == '1'  # set AERONAV_MOTOR_MOCK=0 on real hardware


class MotorCommandWriter:
    """
    Serialises navigation outputs (omega, speed) or manual motor state
    into PWM microsecond commands and sends them to the Arduino over UART.

    Thread-safety: all writes happen in asyncio executor to avoid blocking
    the navigation loop.
    """

    def __init__(self, mock: bool = MOCK_MODE):
        self.mock   = mock
        self._ser   = None
        self._ready = False

    # ── Setup ─────────────────────────────────────────────────────────────────

    def setup(self):
        """Open the serial port.  Call once at startup."""
        if self.mock:
            log.info("MotorCommandWriter in MOCK mode — no serial output")
            return
        try:
            import serial
            self._ser = serial.Serial(MOTOR_UART_PORT, MOTOR_UART_BAUD, timeout=0.1)
            self._ready = True
            log.info("Motor UART ready", port=MOTOR_UART_PORT, baud=MOTOR_UART_BAUD)
        except Exception as e:
            log.error("Motor UART setup failed", exc=str(e), port=MOTOR_UART_PORT)
            self._ready = False

    def close(self):
        """Send a stop command then close the port."""
        if self._ser and self._ser.is_open:
            try:
                self._write_sync(PWM_STOP, PWM_STOP)
                self._ser.close()
                log.info("Motor UART closed — stop command sent")
            except Exception:
                pass

    # ── PWM conversion ────────────────────────────────────────────────────────

    @staticmethod
    def _clamp(value: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, value))

    @staticmethod
    def _pwr_to_us(power: float) -> int:
        """
        Convert normalised power (−1.0 to +1.0) to PWM microseconds.
        0.0 → 1500 (stop), 1.0 → 2000 (full forward), −1.0 → 1000 (full reverse).
        """
        us = PWM_STOP + int(power * 500)
        return max(PWM_MIN, min(PWM_MAX, us))

    def omega_speed_to_pwm(
        self,
        omega_cmd: float,    # degrees/second, positive = turn right
        speed_cmd: float,    # m/s
        max_speed: float,    # m/s (from VesselConfig)
        max_turn:  float,    # deg/s (from VesselConfig)
    ) -> tuple[int, int]:
        """
        Differential-drive mixing: converts (omega, speed) → (left_us, right_us).

        net  = normalised forward speed [−1, 1]
        turn = normalised angular velocity [−1, 1]

        left_pwr  = net + turn   (clamped)
        right_pwr = net − turn   (clamped)

        Positive omega (turn right) → left gets more power, right gets less.
        """
        net  = self._clamp(speed_cmd / max_speed,  -1.0, 1.0) if max_speed > 0 else 0.0
        turn = self._clamp(omega_cmd  / max_turn,  -1.0, 1.0) if max_turn  > 0 else 0.0

        left_pwr  = self._clamp(net + turn, -1.0, 1.0)
        right_pwr = self._clamp(net - turn, -1.0, 1.0)

        return self._pwr_to_us(left_pwr), self._pwr_to_us(right_pwr)

    def manual_to_pwm(
        self,
        left: bool,
        right: bool,
        throttle: float,    # 0.0–1.0
    ) -> tuple[int, int]:
        """
        Convert dashboard manual motor booleans to PWM microseconds.
        Throttle scales the power deviation from 1500.

        left=True,  right=True  → both forward at throttle power
        left=True,  right=False → left forward, right stop
        left=False, right=True  → left stop, right forward
        left=False, right=False → both stop (1500)
        """
        t = self._clamp(throttle, 0.0, 1.0)
        left_us  = self._pwr_to_us(t) if left  else PWM_STOP
        right_us = self._pwr_to_us(t) if right else PWM_STOP
        return left_us, right_us

    # ── Write ─────────────────────────────────────────────────────────────────

    def _write_sync(self, left_us: int, right_us: int):
        """Synchronous UART write — run in executor."""
        frame = f"CMD,{left_us},{right_us}\n"
        if self.mock:
            return   # silent in mock mode
        if self._ser and self._ser.is_open:
            self._ser.write(frame.encode())

    async def write(self, left_us: int, right_us: int):
        """Async write — non-blocking, uses executor for serial I/O."""
        if self.mock:
            return
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._write_sync, left_us, right_us)
        except Exception as e:
            log.warning("Motor UART write error", exc=str(e))
            # Non-fatal: Arduino watchdog will stop motors if commands stop arriving

    async def stop(self):
        """Send stop command (both 1500)."""
        await self.write(PWM_STOP, PWM_STOP)
