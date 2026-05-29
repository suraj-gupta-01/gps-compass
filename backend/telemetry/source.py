"""
Telemetry sources — Mock and UART.

Hardening applied to UartTelemetrySource vs the previous version:

1. Auto-reconnect loop — if the serial port disconnects, source.read() retries
   with backoff instead of crashing the navigation engine.

2. Read timeout — if no data arrives within UART_TIMEOUT_S seconds, raises
   TelemetryTimeout so the engine can decide how to handle stale data.

3. Per-field validation — lat, lng, heading are range-checked before returning.
   A bad parse raises ValueError (caught by engine, previous fix is held).

4. GPS + compass filters are applied HERE so the engine always receives
   clean, filtered values regardless of source.

To switch to real hardware: change MockTelemetrySource → UartTelemetrySource
in main.py.  Nothing else changes.
"""

import math
import time
import asyncio
from abc import ABC, abstractmethod
from telemetry.filters import CompassFilter, GpsFilter
from utils.logger import log


class TelemetryTimeout(Exception):
    pass


class TelemetryReading:
    __slots__ = ('lat', 'lng', 'heading', 'source', 'gps_accepted')
    def __init__(self, lat, lng, heading, source='mock', gps_accepted=True):
        self.lat         = lat
        self.lng         = lng
        self.heading     = heading
        self.source      = source
        self.gps_accepted = gps_accepted


class BaseTelemetrySource(ABC):
    @abstractmethod
    async def read(self) -> TelemetryReading:
        ...
    async def close(self):
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Mock source
# ─────────────────────────────────────────────────────────────────────────────

class MockTelemetrySource(BaseTelemetrySource):
    """
    Drives position from the kinematic model (set via set_position).
    Adds realistic compass noise so heading control behaves like real hardware.
    Filters are applied so the mock closely mirrors what the real source returns.
    """
    def __init__(self):
        self._lat = 37.7749
        self._lng = -122.4194
        self._heading = 0.0
        self._compass_filter = CompassFilter(alpha=0.4)  # slight lag for realism
        self._gps_filter = GpsFilter(max_jump_m=10.0)    # tight for mock

    def set_position(self, lat: float, lng: float, heading: float):
        self._lat = lat
        self._lng = lng
        self._heading = heading

    async def read(self) -> TelemetryReading:
        # No sleep here — engine controls tick rate
        noise = math.sin(time.time() * 4.1) * 2.0 + math.sin(time.time() * 11.3) * 0.8
        raw_heading = (self._heading + noise) % 360
        filtered_heading = self._compass_filter.update(raw_heading)
        lat, lng, accepted = self._gps_filter.update(self._lat, self._lng)
        return TelemetryReading(lat, lng, filtered_heading, 'mock', accepted)


# ─────────────────────────────────────────────────────────────────────────────
# UART source
# ─────────────────────────────────────────────────────────────────────────────

class UartTelemetrySource(BaseTelemetrySource):
    """
    Reads GPS + compass from a microcontroller over UART.

    Expected format (one line per update, 10 Hz recommended):
        LAT,LNG,HEADING\n
        37.774900,-122.419400,045.3\n

    Hardening:
      - Auto-reconnect with exponential backoff on disconnect
      - Read timeout: raises TelemetryTimeout after UART_TIMEOUT_S
      - Per-field range validation before returning
      - Compass EMA filter (alpha configurable)
      - GPS outlier rejection filter

    Configure SERIAL_PORT and BAUD_RATE to match your hardware.
    """

    SERIAL_PORT    = '/dev/ttyAMA0'
    BAUD_RATE      = 115200
    UART_TIMEOUT_S = 2.0          # seconds before declaring timeout
    RECONNECT_DELAY_S = 3.0       # initial reconnect wait
    MAX_RECONNECT_DELAY_S = 30.0  # cap backoff at 30s

    def __init__(self, compass_alpha: float = 0.3, max_gps_jump_m: float = 25.0):
        self._ser = None
        self._compass_filter = CompassFilter(alpha=compass_alpha)
        self._gps_filter     = GpsFilter(max_jump_m=max_gps_jump_m)
        self._reconnect_delay = self.RECONNECT_DELAY_S
        self._connect()

    def _connect(self):
        try:
            import serial
            self._ser = serial.Serial(
                self.SERIAL_PORT,
                self.BAUD_RATE,
                timeout=self.UART_TIMEOUT_S,
            )
            self._reconnect_delay = self.RECONNECT_DELAY_S  # reset backoff
            log.info("UART connected", port=self.SERIAL_PORT, baud=self.BAUD_RATE)
        except Exception as e:
            self._ser = None
            log.error("UART connect failed", exc=str(e), port=self.SERIAL_PORT)

    async def _reconnect(self):
        log.warning("UART reconnecting", delay_s=self._reconnect_delay)
        await asyncio.sleep(self._reconnect_delay)
        self._reconnect_delay = min(
            self._reconnect_delay * 2,
            self.MAX_RECONNECT_DELAY_S,
        )
        self._connect()

    def _readline_sync(self) -> str:
        """Blocking readline — called in executor to avoid blocking asyncio loop."""
        if self._ser is None or not self._ser.is_open:
            raise ConnectionError("Serial port not open")
        line = self._ser.readline()
        return line.decode('utf-8', errors='replace')

    @staticmethod
    def _parse(line: str) -> tuple[float, float, float]:
        parts = line.strip().split(',')
        if len(parts) < 3:
            raise ValueError(f"Short UART frame: {line!r}")
        lat = float(parts[0])
        lng = float(parts[1])
        hdg = float(parts[2])
        # Range validation — reject physically impossible values
        if not (-90.0 <= lat <= 90.0):
            raise ValueError(f"lat out of range: {lat}")
        if not (-180.0 <= lng <= 180.0):
            raise ValueError(f"lng out of range: {lng}")
        if not math.isfinite(hdg):
            raise ValueError(f"heading not finite: {hdg}")
        return lat, lng, hdg % 360

    async def read(self) -> TelemetryReading:
        loop = asyncio.get_event_loop()

        try:
            raw_line = await loop.run_in_executor(None, self._readline_sync)
        except ConnectionError:
            await self._reconnect()
            raise TelemetryTimeout("UART not connected")
        except Exception as e:
            log.warning("UART read error", exc=str(e))
            await self._reconnect()
            raise TelemetryTimeout(f"UART read failed: {e}")

        # Empty line = timeout (pyserial returns b'' on timeout)
        if not raw_line.strip():
            raise TelemetryTimeout("UART read timeout — no data")

        try:
            raw_lat, raw_lng, raw_hdg = self._parse(raw_line)
        except ValueError as e:
            log.warning("UART parse error", exc=str(e), line=raw_line[:40])
            raise  # engine will hold previous fix

        # Apply filters
        lat, lng, gps_ok = self._gps_filter.update(raw_lat, raw_lng)
        hdg = self._compass_filter.update(raw_hdg)

        if not gps_ok:
            log.debug("GPS outlier rejected",
                      raw_lat=raw_lat, raw_lng=raw_lng)

        return TelemetryReading(lat, lng, hdg, 'uart', gps_ok)

    async def close(self):
        if self._ser and self._ser.is_open:
            try:
                self._ser.close()
            except Exception:
                pass
        log.info("UART closed")
