"""
API routes — REST and WebSocket.
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


# ── Mission upload ─────────────────────────────────────────────────────────────

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


# ── Auto mission commands ──────────────────────────────────────────────────────

class CommandBody(BaseModel):
    command: str

@router.post("/api/command")
async def command(body: CommandBody):
    engine = get_engine()
    cmd = body.command.lower().strip()

    # Block start/resume if RC has hardware control
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


# ── Manual mode endpoints ──────────────────────────────────────────────────────

@router.post("/api/manual/enter")
async def manual_enter():
    """
    Enter software manual override via dashboard.
    Note: if RC transmitter is active, hardware already has control —
    this call is informational only (engine will already be in MANUAL
    from the RC monitor's enter_manual() call).
    """
    engine = get_engine()
    if engine._mode == OperatingMode.MANUAL:
        return {"ok": True, "message": "Already in manual mode."}
    engine.enter_manual()
    log.info("Manual mode entered via dashboard API")
    return {"ok": True, "mode": "manual"}


@router.post("/api/manual/exit")
async def manual_exit():
    """
    Exit software manual mode and return to AUTO/paused.
    Blocked if RC transmitter is still active — hardware still has control.
    """
    engine = get_engine()

    # If RC is still active, don't allow software exit
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
    """
    Set dashboard manual motor state (software override only).
    Has no effect on hardware when RC transmitter is in control —
    the RC receiver drives the ESCs directly, bypassing the Pi entirely.
    """
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
    """Emergency stop in software. Stays in manual mode."""
    engine = get_engine()
    engine.manual_ctrl.stop()
    log.info("Manual emergency stop via dashboard")
    return {"ok": True, "stopped": True}


# ── Health ─────────────────────────────────────────────────────────────────────

@router.get("/health")
async def health():
    engine = get_engine()
    rc_status = _rc_monitor.status_dict() if _rc_monitor else {"rc_monitor_active": False}
    return {"status": "ok", **engine.health_dict(), "rc": rc_status}


# ── WebSocket ──────────────────────────────────────────────────────────────────

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
    except Exception:
        pass
    finally:
        engine.remove_client(websocket)
        log.info("WS client disconnected", clients=len(engine._clients))
