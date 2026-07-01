"""
AeroNav Mission Execution Backend.

What it does:
    Creates the FastAPI app, NavigationEngine, telemetry source, RC monitor,
    REST routes, WebSocket route, and AeroSim HIL bridge.

Imports from:
    FastAPI/CORS/asyncio, navigation.engine, navigation.kinematics,
    telemetry.source, telemetry.rc_monitor, api.routes, api.sim_bridge,
    utils.logger.

Behavior:
    Existing backend startup behavior is preserved; including sim_router is
    additive and exposes passive /api/sim/* HIL endpoints.

Run:
    cd backend
    uvicorn main:app --host 0.0.0.0 --port 8000

Telemetry source  → change MockTelemetrySource to UartTelemetrySource for real hardware
Motor commands    → set MOCK_MODE = False in navigation/motor_writer.py
RC monitor        → set MOCK_MODE = False in telemetry/rc_monitor.py, wire GPIO 17

UART port assignments on Raspberry Pi 5:
  /dev/ttyAMA0   GPS + compass   IN   (STM32 TX → Pi RX)
  /dev/ttyAMA2   Motor commands  OUT  (Pi TX → STM32 RX)
  GPIO 17        RC mode detect  IN   (RC receiver CH5 → Pi GPIO)

Enable ttyAMA2 by adding to /boot/firmware/config.txt:
  dtoverlay=uart2
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from navigation.engine import NavigationEngine
from navigation.kinematics import VesselConfig
from telemetry.source import MockTelemetrySource   # ← swap for UartTelemetrySource
from telemetry.rc_monitor import RCMonitor
from api.routes import router, set_engine, set_rc_monitor
from api.sim_bridge import sim_router
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
telemetry_source = MockTelemetrySource()
# from telemetry.source import UartTelemetrySource
# telemetry_source = UartTelemetrySource()

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
app.include_router(sim_router)
