"""
AeroNav Mission Execution Backend
Run: uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from navigation.engine import NavigationEngine
from telemetry.source import MockTelemetrySource  # swap for UartTelemetrySource on hardware
from api.routes import router, set_engine

# ── Choose telemetry source ────────────────────────────────────────────────────
# For real hardware: replace MockTelemetrySource() with UartTelemetrySource()
telemetry_source = MockTelemetrySource()
engine = NavigationEngine(telemetry_source)

@asynccontextmanager
async def lifespan(app: FastAPI):
    set_engine(engine)
    task = asyncio.create_task(engine.run())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await telemetry_source.close()

app = FastAPI(title='AeroNav Execution Backend', lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)

app.include_router(router)

@app.get('/health')
async def health():
    return {'status': 'ok', 'engine': 'running'}