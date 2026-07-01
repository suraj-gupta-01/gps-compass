"""
API routes — REST and WebSocket.

Changes from original
─────────────────────
1. New /api/debug/perception endpoints for mock detection injection.
   These are safe to call in production (they are no-ops if MOCK_DETECTIONS
   is False) and are primarily used during ground validation without hardware.

2. /health now includes perception and behavior status (additive fields).

All original endpoints are UNCHANGED.  No existing frontend calls break.
"""

import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
from mission.models import MissionJSON
from navigation.engine import NavigationEngine, OperatingMode
from utils.logger import log

router = APIRouter()

_engine:     NavigationEngine = None
_rc_monitor                   = None

def set_engine(engine: NavigationEngine):
    global _engine
    _engine = engine

def set_rc_monitor(monitor):
    global _rc_monitor
    _rc_monitor = monitor

def get_engine() -> NavigationEngine:
    if _engine is None:
        raise RuntimeError("Engine not initialised")
    return _engine


# ── Mission upload (unchanged) ─────────────────────────────────────────────────

@router.post("/api/mission")
async def upload_mission(mission: MissionJSON):
    engine = get_engine()
    nav_st = engine.sequencer.nav_state()
    if (nav_st in ("navigating", "coverage", "returning")
            and not engine._paused
            and engine._mode == OperatingMode.AUTO):
        raise HTTPException(
            status_code=409,
            detail="Mission running. Pause or reset before loading a new mission.",
        )
    path = engine.load_mission(mission)
    segs = len(mission.mission)
    log.info("Mission uploaded", points=len(path), segments=segs)
    return JSONResponse({
        "ok": True,
        "total_points": len(path),
        "segments": segs,
        "message": f"Loaded {segs} segment(s), {len(path)} path points.",
        "generated_path": path,
    })


# ── Auto mission commands (unchanged) ─────────────────────────────────────────

class CommandBody(BaseModel):
    command: str

@router.post("/api/command")
async def command(body: CommandBody):
    engine = get_engine()
    cmd = body.command.lower().strip()

    if _rc_monitor and _rc_monitor._rc_active and cmd in ("start", "resume"):
        raise HTTPException(
            status_code=409,
            detail="RC transmitter has hardware control. Release RC before resuming auto.",
        )

    if engine._mode == OperatingMode.MANUAL and cmd in ("start", "resume"):
        raise HTTPException(
            status_code=409,
            detail="Vessel is in MANUAL mode. Call /api/manual/exit first.",
        )

    if cmd == "start":
        if not engine.sequencer.loaded:
            raise HTTPException(status_code=400, detail="No mission loaded.")
        engine.start()
    elif cmd == "pause":
        engine.pause()
    elif cmd == "resume":
        if not engine.sequencer.loaded:
            raise HTTPException(status_code=400, detail="No mission loaded.")
        engine.resume()
    elif cmd == "reset":
        engine.reset()
    else:
        raise HTTPException(status_code=400, detail=f"Unknown command: {cmd!r}")

    log.info("Command received", command=cmd)
    return {"ok": True, "command": cmd}


# ── Manual mode endpoints (unchanged) ─────────────────────────────────────────

@router.post("/api/manual/enter")
async def manual_enter():
    engine = get_engine()
    if engine._mode == OperatingMode.MANUAL:
        return {"ok": True, "message": "Already in manual mode."}
    engine.enter_manual()
    log.info("Manual mode entered via dashboard API")
    return {"ok": True, "mode": "manual"}


@router.post("/api/manual/exit")
async def manual_exit():
    engine = get_engine()
    if _rc_monitor and _rc_monitor._rc_active:
        raise HTTPException(
            status_code=409,
            detail="RC transmitter is still active. Switch transmitter to AUTO position first.",
        )
    if engine._mode == OperatingMode.AUTO:
        return {"ok": True, "message": "Already in AUTO mode."}
    engine.resume_auto()
    log.info("Manual mode exited via dashboard API")
    return {
        "ok": True,
        "mode": "auto",
        "reanchored_index": engine.sequencer.current_index,
        "message": "Returned to AUTO (paused). Send resume to continue mission.",
    }


class MotorBody(BaseModel):
    left:     bool
    right:    bool
    throttle: Optional[float] = 0.5

@router.post("/api/manual/motor")
async def manual_motor(body: MotorBody):
    engine = get_engine()
    if engine._mode != OperatingMode.MANUAL:
        raise HTTPException(
            status_code=409,
            detail="Not in MANUAL mode. Call /api/manual/enter first.",
        )
    rc_has_control = _rc_monitor and _rc_monitor._rc_active
    engine.manual_set_left(body.left)
    engine.manual_set_right(body.right)
    if body.throttle is not None:
        engine.manual_set_throttle(body.throttle)
    return {
        "ok": True,
        "left": body.left,
        "right": body.right,
        "throttle": engine.manual_ctrl.state.throttle,
        "note": "RC transmitter has hardware control — dashboard commands update display only"
                if rc_has_control else "Dashboard motor commands active",
    }


@router.post("/api/manual/stop")
async def manual_stop():
    engine = get_engine()
    engine.manual_ctrl.stop()
    log.info("Manual emergency stop via dashboard")
    return {"ok": True, "stopped": True}


# ── Health (extended — additive, backward compatible) ─────────────────────────

@router.get("/health")
async def health():
    engine = get_engine()
    rc_status = _rc_monitor.status_dict() if _rc_monitor else {"rc_monitor_active": False}
    return {"status": "ok", **engine.health_dict(), "rc": rc_status}


# ── Debug / perception mock injection ─────────────────────────────────────────
# These endpoints exist for ground validation without Hailo hardware.
# They inject observations directly into the PerceptionManager, bypassing
# the Hailo queue entirely.  They have NO effect on telemetry routing —
# all decision-making remains onboard.

class MockObstacleBody(BaseModel):
    cx_norm:    float = 0.0    # [-1, +1], 0 = frame centre
    width_frac: float = 0.20   # [0, 1], fraction of frame width

class MockTrashBody(BaseModel):
    cx_norm:   float = 0.05   # [-1, +1]
    area_frac: float = 0.06   # [0, 1]

@router.post("/api/debug/inject_obstacle")
async def debug_inject_obstacle(body: MockObstacleBody):
    """
    Inject a mock obstacle observation for testing without Hailo hardware.
    The BehaviorManager will immediately respond as if Hailo detected an obstacle.
    """
    engine = get_engine()
    engine.perception.inject_mock_obstacle(
        cx_norm=body.cx_norm,
        width_frac=body.width_frac,
    )
    log.info("Debug: mock obstacle injected",
             cx_norm=body.cx_norm, width_frac=body.width_frac)
    return {"ok": True, "injected": "obstacle",
            "cx_norm": body.cx_norm, "width_frac": body.width_frac}


@router.post("/api/debug/inject_trash")
async def debug_inject_trash(body: MockTrashBody):
    """
    Inject a mock trash observation for testing without Hailo hardware.
    The BehaviorManager will immediately respond as if Hailo detected trash.
    """
    engine = get_engine()
    engine.perception.inject_mock_trash(
        cx_norm=body.cx_norm,
        area_frac=body.area_frac,
    )
    log.info("Debug: mock trash injected",
             cx_norm=body.cx_norm, area_frac=body.area_frac)
    return {"ok": True, "injected": "trash",
            "cx_norm": body.cx_norm, "area_frac": body.area_frac}


@router.post("/api/debug/clear_mock")
async def debug_clear_mock():
    """Clear any injected mock observations."""
    engine = get_engine()
    engine.perception.clear_mock()
    log.info("Debug: mock observations cleared")
    return {"ok": True, "cleared": True}


@router.get("/api/debug/perception")
async def debug_perception_status():
    """Return current perception and behavior status for dashboard debug panel."""
    engine = get_engine()
    return {
        "ok": True,
        **engine.perception.status_dict(),
        **engine.behavior.status_dict(),
    }


# ── WebSocket (unchanged) ──────────────────────────────────────────────────────

@router.websocket("/ws/telemetry")
async def ws_telemetry(websocket: WebSocket):
    engine = get_engine()
    await websocket.accept()
    engine.add_client(websocket)
    log.info("WS client connected", clients=len(engine._clients))
    try:
        while True:
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.warning("WS handler error", exc=str(e))
    finally:
        engine.remove_client(websocket)
        log.info("WS client disconnected", clients=len(engine._clients))
