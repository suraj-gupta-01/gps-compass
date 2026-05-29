"""
RCMonitor — detects when the RC transmitter/receiver has taken physical motor control.

Physical setup
──────────────
The RC receiver has a dedicated "mode channel" output (often CH5 or CH6 on hobby
receivers).  Wire this channel's signal wire to a Pi GPIO input pin (default: GPIO 17).

  RC Receiver CH5 signal ──────────────────────► Pi GPIO 17 (input, pull-down)
  RC Receiver GND ────────────────────────────── Pi GND

When the transmitter is in MANUAL position:
  GPIO 17 reads HIGH  →  RC is in control  →  engine enters MANUAL mode
When the transmitter is in AUTO position:
  GPIO 17 reads LOW   →  RC released       →  engine returns to AUTO

Why a dedicated GPIO signal and not something software-based?
  The RC receiver drives the ESCs/motors directly through its own wiring — the Pi
  has no electrical connection to the motors at all.  The only way the Pi can know
  that the RC has taken over is through a separate signal wire from the receiver.
  This is the same pattern used by ArduPilot, Pixhawk, and similar autopilots.

Mock mode
─────────
When MOCK_MODE = True (no Pi hardware), the monitor does nothing.  Manual mode
is still available via the dashboard REST API (/api/manual/enter).

Debounce
────────
RC receivers can produce brief glitches when the operator moves the mode switch.
A 200 ms debounce (DEBOUNCE_S) prevents the engine from toggling in/out of manual
on a transient.

How it integrates
─────────────────
RCMonitor runs as a background asyncio task alongside the navigation engine loop.
It polls the GPIO pin every POLL_INTERVAL_S and calls engine.enter_manual() or
engine.resume_auto() on genuine state transitions.  It does NOT touch the motor
output — the RC receiver does that entirely independently through its own wiring.

Wiring diagram
──────────────

  ┌─────────────────────┐         ┌──────────────────────┐
  │   RC Transmitter    │  radio  │    RC Receiver        │
  │   (operator holds)  │ ──────► │                       │
  └─────────────────────┘         │  CH1 ──► Left ESC    │
                                  │  CH2 ──► Right ESC   │
                                  │  CH5 ──► GPIO 17 ──► Pi GPIO (mode detect)
                                  │  GND ───────────────── Pi GND
                                  └──────────────────────┘

The Pi reads GPIO 17 only to know the mode.
The Pi does NOT control the motors in any mode — the RC receiver does that directly.
"""

import asyncio
import time
from utils.logger import log

# ── Configuration ─────────────────────────────────────────────────────────────

GPIO_PIN        = 17     # BCM pin number connected to RC receiver mode channel
POLL_INTERVAL_S = 0.05   # 20 Hz polling (fast enough to catch mode switch)
DEBOUNCE_S      = 0.20   # ignore transitions shorter than this (200 ms)
MOCK_MODE       = True   # set False on real Raspberry Pi hardware


class RCMonitor:
    """
    Polls a GPIO pin to detect RC transmitter/receiver mode state.
    Calls engine.enter_manual() or engine.resume_auto() on genuine transitions.

    Designed to run as a long-lived asyncio background task.
    """

    def __init__(self, engine, gpio_pin: int = GPIO_PIN, mock: bool = MOCK_MODE):
        self.engine     = engine
        self.gpio_pin   = gpio_pin
        self.mock       = mock
        self._gpio      = None          # RPi.GPIO handle
        self._rc_active = False         # current debounced state
        self._last_raw  = False         # last raw GPIO reading
        self._transition_t = 0.0        # when raw state last changed

    # ── GPIO setup ────────────────────────────────────────────────────────────

    def _setup_gpio(self):
        """Initialise RPi.GPIO.  Called once at task start."""
        try:
            import RPi.GPIO as GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.gpio_pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            self._gpio = GPIO
            log.info("RC monitor GPIO ready", pin=self.gpio_pin)
        except ImportError:
            log.warning("RPi.GPIO not available — RC monitor running in mock mode")
            self.mock = True
        except Exception as e:
            log.error("RC monitor GPIO setup failed", exc=str(e))
            self.mock = True

    def _cleanup_gpio(self):
        if self._gpio is not None:
            try:
                self._gpio.cleanup(self.gpio_pin)
            except Exception:
                pass

    def _read_pin(self) -> bool:
        """Read current GPIO state.  Returns True if RC is commanding manual."""
        if self.mock or self._gpio is None:
            return False   # mock: RC never active
        try:
            return bool(self._gpio.input(self.gpio_pin))
        except Exception as e:
            log.warning("GPIO read error", exc=str(e))
            return self._rc_active   # hold last known state on read error

    # ── Debounce ──────────────────────────────────────────────────────────────

    def _debounced(self, raw: bool) -> bool:
        """
        Return the debounced state.  A raw transition must hold for DEBOUNCE_S
        before it is accepted as a genuine mode change.
        """
        now = time.monotonic()
        if raw != self._last_raw:
            # Raw state changed — start the debounce timer
            self._last_raw     = raw
            self._transition_t = now
            return self._rc_active   # not yet accepted

        # Raw state has been stable — check if debounce period elapsed
        if (now - self._transition_t) >= DEBOUNCE_S:
            return raw   # accept the new state
        return self._rc_active   # still in debounce window

    # ── Main task ─────────────────────────────────────────────────────────────

    async def run(self):
        """
        Long-running asyncio task.  Call as:
            asyncio.create_task(rc_monitor.run())
        """
        if not self.mock:
            self._setup_gpio()

        if self.mock:
            log.info("RC monitor running in MOCK mode — GPIO pin not monitored")
            # In mock mode, do nothing; manual override via REST API only.
            return

        log.info("RC monitor started", pin=self.gpio_pin, debounce_ms=int(DEBOUNCE_S * 1000))

        try:
            while True:
                await asyncio.sleep(POLL_INTERVAL_S)

                raw      = self._read_pin()
                debounced = self._debounced(raw)

                if debounced == self._rc_active:
                    continue   # no state change

                # Genuine transition
                self._rc_active = debounced

                if self._rc_active:
                    # RC has taken control → tell engine to enter MANUAL
                    # (autonomous navigation suspended; Pi reads GPS only)
                    log.info("RC override ACTIVE — entering MANUAL mode")
                    self.engine.enter_manual()
                else:
                    # RC released → return to AUTO/paused; re-anchor sequencer
                    log.info("RC override RELEASED — returning to AUTO (paused)")
                    self.engine.resume_auto()
                    # Note: engine is paused. The operator must explicitly
                    # start/resume via the dashboard or a separate command.

        except asyncio.CancelledError:
            log.info("RC monitor stopped")
        finally:
            self._cleanup_gpio()

    # ── Status for health endpoint ─────────────────────────────────────────────

    def status_dict(self) -> dict:
        return {
            'rc_monitor_active': not self.mock,
            'rc_override':       self._rc_active,
            'gpio_pin':          self.gpio_pin,
            'mock':              self.mock,
        }
