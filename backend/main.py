"""
AeroNav Mission Execution Backend

Run:
    cd backend
    uvicorn main:app --host 0.0.0.0 --port 8000

Telemetry source  → set AERONAV_USE_REAL_TELEMETRY=1 for real hardware (USB-RS232)
Motor commands    → set AERONAV_MOTOR_MOCK=0 in environment for real hardware
RC monitor        → set MOCK_MODE = False in telemetry/rc_monitor.py, wire GPIO 17

Port assignments:
  /dev/ttyAMA0    Motor commands  OUT  (Pi TX → Arduino RX)
  /dev/ttyUSBx    GPS + compass   IN   (STM32 → USB-RS232 → Pi USB)
  GPIO 17         RC mode detect  IN   (RC receiver CH5 → Pi GPIO)

Configure AERONAV_GPS_PORT env var for a stable USB-serial path (or udev symlink).
"""

import os
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from navigation.engine import NavigationEngine
from navigation.kinematics import VesselConfig
from telemetry.source import MockTelemetrySource, UartTelemetrySource
from telemetry.rc_monitor import RCMonitor
from api.routes import router, set_engine, set_rc_monitor
from utils.logger import log

# ── Vessel configuration ───────────────────────────────────────────────────────
vessel_cfg = VesselConfig(
    cruise_speed_mps   = 1.5,
    max_speed_mps      = 2.5,
    speed_tau          = 2.0,
    max_turn_rate_dps  = 15.0,
    turn_speed_factor  = 0.6,
    lookahead_m        = 8.0,
    waypoint_radius_m  = 4.0,
    heading_kp         = 0.4,
)

# ── Sources ────────────────────────────────────────────────────────────────────
USE_REAL_TELEMETRY = os.environ.get('AERONAV_USE_REAL_TELEMETRY', '0') == '1'
telemetry_source = UartTelemetrySource() if USE_REAL_TELEMETRY else MockTelemetrySource()

engine     = NavigationEngine(telemetry_source, config=vessel_cfg)
rc_monitor = RCMonitor(engine)   # GPIO mock=True by default


@asynccontextmanager
async def lifespan(app: FastAPI):
    set_engine(engine)
    set_rc_monitor(rc_monitor)
    log.info("AeroNav backend starting")

    nav_task = asyncio.create_task(engine.run())
    rc_task  = asyncio.create_task(rc_monitor.run())

    yield

    log.info("AeroNav backend shutting down")
    nav_task.cancel()
    rc_task.cancel()
    for task in (nav_task, rc_task):
        try:
            await task
        except asyncio.CancelledError:
            pass
    await telemetry_source.close()


app = FastAPI(title="AeroNav Execution Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
