"""
HIL simulator bridge for AeroSim.

What it does:
  Exposes passive /api/sim/* endpoints for browser-driven hardware-in-the-loop
  tests against the real NavigationEngine.

Imports from:
  FastAPI/Pydantic for route schemas, api.routes.get_engine for the live engine,
  telemetry.source.MockTelemetrySource for safe synthetic GPS injection.

Behavior:
  Purely additive. It updates mock-only sensor/perception inputs and reads the
  engine's latest motor/status state without changing navigation logic.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from api.routes import get_engine
from telemetry.source import MockTelemetrySource


sim_router = APIRouter()


class SimTelemetryBody(BaseModel):
    lat: float
    lng: float
    heading: float


class SimObstacleBody(BaseModel):
    cx_norm: float
    width_frac: float
    confidence: float = 0.85


class SimTrashBody(BaseModel):
    cx_norm: float
    cy_norm: float = 0.0
    area_frac: float
    confidence: float = 0.80


class SimPerceptionBody(BaseModel):
    obstacles: list[SimObstacleBody] = []
    trash: list[SimTrashBody] = []


@sim_router.post("/api/sim/telemetry")
async def sim_telemetry(body: SimTelemetryBody):
    engine = get_engine()
    if isinstance(engine.source, MockTelemetrySource):
        engine.source.set_position(body.lat, body.lng, body.heading)
    return {"ok": True}


@sim_router.post("/api/sim/perception")
async def sim_perception(body: SimPerceptionBody):
    engine = get_engine()

    if not body.obstacles and not body.trash:
        engine.perception.clear_mock()
    else:
        # Mock observations persist only because Prompt 1 fix #5 added mock flags
        # in PerceptionManager. AeroSim still re-sends every tick so a one-tick
        # implementation remains usable.
        engine.perception.clear_mock()
        for obs in body.obstacles:
            engine.perception.inject_mock_obstacle(
                cx_norm=obs.cx_norm,
                width_frac=obs.width_frac,
            )
        for tr in body.trash:
            engine.perception.inject_mock_trash(
                cx_norm=tr.cx_norm,
                area_frac=tr.area_frac,
            )

    return {
        "ok": True,
        "obstacles_injected": len(body.obstacles),
        "trash_injected": len(body.trash),
    }


@sim_router.get("/api/sim/motor_cmd")
async def sim_motor_cmd():
    engine = get_engine()
    return engine.last_motor_cmd


@sim_router.get("/api/sim/status")
async def sim_status():
    engine = get_engine()
    source_type = "mock" if isinstance(engine.source, MockTelemetrySource) else "uart"
    return {
        "source_type": source_type,
        "motor_mock": bool(engine.motor_writer.mock),
        "perception_mock_active": bool(
            getattr(engine.perception, "_mock_obstacle_active", False)
            or getattr(engine.perception, "_mock_trash_active", False)
        ),
        "engine_running": bool(engine._running),
        "engine_paused": bool(engine._paused),
        "mission_loaded": bool(engine.sequencer.loaded),
        "nav_state": engine.sequencer.nav_state(),
    }
