from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from mission.models import MissionJSON
from navigation.engine import NavigationEngine
from typing import Any

router = APIRouter()

# Single shared engine instance (injected from main.py)
_engine: NavigationEngine | None = None

def set_engine(engine: NavigationEngine):
    global _engine
    _engine = engine

def get_engine() -> NavigationEngine:
    if _engine is None:
        raise RuntimeError('Engine not initialised')
    return _engine


@router.post('/api/mission')
async def upload_mission(mission: MissionJSON):
    engine = get_engine()
    path = engine.load_mission(mission)
    segs = len(mission.mission)
    return JSONResponse({
        'ok': True,
        'total_points': len(path),
        'segments': segs,
        'message': f'Loaded {segs} segment(s), {len(path)} path points.',
        'generated_path': path,
    })


class CommandBody(BaseModel):
    command: str

@router.post('/api/command')
async def command(body: CommandBody):
    engine = get_engine()
    cmd = body.command.lower()
    if cmd == 'start':
        engine.start()
    elif cmd == 'pause':
        engine.pause()
    elif cmd == 'resume':
        engine.resume()
    elif cmd == 'reset':
        engine.reset()
    else:
        raise HTTPException(status_code=400, detail=f'Unknown command: {cmd}')
    return {'ok': True, 'command': cmd}


@router.websocket('/ws/telemetry')
async def ws_telemetry(websocket: WebSocket):
    engine = get_engine()
    await websocket.accept()
    engine.add_client(websocket)
    try:
        while True:
            # Keep alive — client can send pings
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        engine.remove_client(websocket)