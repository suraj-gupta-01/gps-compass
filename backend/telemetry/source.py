"""
Abstract telemetry source + mock implementation.

To add real UART:
  1. Set USE_MOCK = False in your .env or config.
  2. Implement UartTelemetrySource below.
  3. Pass it to the router in api/routes.py.
"""

import math
import time
import asyncio
from abc import ABC, abstractmethod
from typing import Tuple, Optional

class TelemetryReading:
    __slots__ = ('lat','lng','heading','source')
    def __init__(self, lat, lng, heading, source='mock'):
        self.lat = lat
        self.lng = lng
        self.heading = heading
        self.source = source

class BaseTelemetrySource(ABC):
    @abstractmethod
    async def read(self) -> TelemetryReading:
        ...

    async def close(self):
        pass


class MockTelemetrySource(BaseTelemetrySource):
    """
    Simulates a vessel moving along the mission path for testing.
    Position is driven by the sequencer (set via set_position),
    so this class only provides the heading with a small noise.
    """
    def __init__(self):
        self._lat = 37.7749
        self._lng = -122.4194
        self._heading = 0.0
        self._noise = 0.0

    def set_position(self, lat: float, lng: float, heading: float):
        self._lat = lat
        self._lng = lng
        self._heading = heading

    async def read(self) -> TelemetryReading:
        await asyncio.sleep(0.05)   # 20 Hz max
        # Small heading noise for realism
        noise = math.sin(time.time() * 3.7) * 1.5
        return TelemetryReading(
            lat=self._lat,
            lng=self._lng,
            heading=(self._heading + noise) % 360,
            source='mock',
        )


class UartTelemetrySource(BaseTelemetrySource):
    """
    Real UART source — replace MockTelemetrySource with this for hardware.

    Expected serial format (one line per message):
        LAT,LNG,HEADING\n
    e.g.: 37.774900,-122.419400,045.3\n

    Configure SERIAL_PORT and BAUD_RATE to match your microcontroller.
    """
    SERIAL_PORT = '/dev/ttyACM0'   # Raspberry Pi UART0
    BAUD_RATE   = 115200

    def __init__(self):
        import serial  # imported lazily so mock mode doesn't require pyserial
        self._ser = serial.Serial(self.SERIAL_PORT, self.BAUD_RATE, timeout=1)

    async def read(self) -> TelemetryReading:
        loop = asyncio.get_event_loop()
        line = await loop.run_in_executor(None, self._readline)
        parts = line.strip().split(',')
        if len(parts) < 3:
            raise ValueError(f'Bad UART frame: {line!r}')
        return TelemetryReading(
            lat=float(parts[0]),
            lng=float(parts[1]),
            heading=float(parts[2]) % 360,
            source='uart',
        )

    def _readline(self) -> str:
        return self._ser.readline().decode('utf-8', errors='replace')

    async def close(self):
        self._ser.close()
