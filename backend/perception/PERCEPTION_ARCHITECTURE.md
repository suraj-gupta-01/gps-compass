# AeroNav Perception & Behavior Architecture

## Overview

This document describes the perception-aware autonomy stack added to the
AeroNav mission execution system.  All original mission logic, coverage,
waypoint navigation, telemetry, and manual override functionality is
**preserved without modification**.  The new stack is inserted as a thin
arbitration layer between the PurePursuitController output and the motor writer.

---

## File Index

| File | Role | New / Modified |
|------|------|---------------|
| `config.py` | Centralised runtime config for all tuning params | **New** |
| `perception/detection.py` | Detection dataclasses (BBox, RawDetection, Observations) | **New** |
| `perception/hailo_runner.py` | Hailo-8L inference thread + mock mode | **New** |
| `perception/perception_manager.py` | Detection drain, confirm/decay, publish observations | **New** |
| `perception/trash_tracker.py` | Visual-servoing approach controller | **New** |
| `behavior/behavior_manager.py` | State-machine arbitration, single motor authority | **New** |
| `navigation/engine.py` | Integrates perception + behavior into tick loop | **Modified** |
| `api/routes.py` | Adds `/api/debug/*` mock injection endpoints | **Modified** |

Unchanged files: `controller.py`, `kinematics.py`, `manual.py`, `motor_writer.py`,
`filters.py`, `rc_monitor.py`, `source.py`, `geo.py`, `logger.py`, `main.py`,
`sequencer.py`, `lawnmower.py`, `models.py`

---

## Thread Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  OS Thread: hailo-inference   (daemon, non-asyncio)                 │
│                                                                      │
│  Camera (CSI / V4L2)                                                 │
│      │                                                               │
│      ▼  raw frame (uint8 BGR)                                        │
│  Letterbox + normalise  ──►  Hailo-8L ASIC  ──►  NMS output tensor │
│                                                        │             │
│                              Pi CPU: parse tensor      │             │
│                              → RawDetection dataclass  │             │
│                                                        ▼             │
│                              queue.Queue(maxsize=2) ◄──┘             │
└─────────────────────────────────────────────────────────────────────┘
                                        │
                                 (thread-safe)
                                        │ queue.get_nowait()
                                        ▼
┌─────────────────────────────────────────────────────────────────────┐
│  asyncio event loop  (main thread)                                  │
│                                                                      │
│  NavigationEngine._tick_auto()  [10 Hz]                             │
│      │                                                               │
│      ├─► PerceptionManager.update()                                 │
│      │       drain queue (non-blocking)                              │
│      │       confirm/decay tracking                                  │
│      │       → ObstacleObservation | None                           │
│      │       → TrashObservation    | None                           │
│      │                                                               │
│      ├─► PurePursuitController (unchanged)                          │
│      │       → pp_omega, pp_speed                                   │
│      │                                                               │
│      ├─► BehaviorManager.arbitrate()                                │
│      │       state machine (NAVIGATE / TRACK_TRASH /                │
│      │       COLLECT_TRASH / AVOID_OBSTACLE / RETURN_TO_PATH)       │
│      │       → MotorCmd (omega_cmd, speed_cmd, left, right)         │
│      │                                                               │
│      ├─► VesselKinematics.step(omega_cmd, speed_cmd)               │
│      │                                                               │
│      └─► MotorCommandWriter.write(left_us, right_us)  [STM32]      │
│          WebSocket broadcast [GCS]                                   │
└─────────────────────────────────────────────────────────────────────┘
```

### CPU / Hailo workload split

| Work item | Where |
|-----------|-------|
| Frame capture + letterbox resize | Pi CPU (camera thread) |
| YOLO inference + NMS | **Hailo-8L ASIC** |
| Tensor parse → RawDetection | Pi CPU (≈ 10 µs) |
| Confirm/decay tracking | Pi CPU (≈ 5 µs/tick) |
| State machine arbitration | Pi CPU (≈ 10 µs/tick) |
| Pure-pursuit + kinematics | Pi CPU (≈ 50 µs/tick) |
| Telemetry broadcast | Pi CPU (asyncio, I/O bound) |

Pi CPU is fully free during the Hailo inference (~30ms per frame at 10 FPS cap).

---

## State Machine

```
                   ┌─────────────────────────────────────┐
                   │           AVOID_OBSTACLE             │◄─── from ANY state
                   │  (highest priority)                  │     [obstacle detected]
                   │  Reactive potential-field steering   │
                   └───────────────┬─────────────────────┘
                                   │ obstacle clears (N frames)
                                   ▼
                   ┌─────────────────────────────────────┐
        ┌─────────►│         RETURN_TO_PATH              │◄──── after trash collected
        │          │  Re-anchor to nearest path point    │
        │          │  Pass through PP commands           │
        │          └───────────────┬─────────────────────┘
        │                          │ re-anchor done
        │                          ▼
        │          ┌─────────────────────────────────────┐
        │          │            NAVIGATE                  │
        │          │  Pure-pursuit (default / transparent)│
        │          └───────────────┬─────────────────────┘
        │                          │ trash confirmed
        │                          ▼
        │          ┌─────────────────────────────────────┐
        │          │           TRACK_TRASH               │
        │          │  Visual servoing toward trash        │
        │          │  omega = KP * error_x (px)          │
        │          └───────────────┬─────────────────────┘
        │                          │ area_frac >= threshold
        │                          ▼
        │          ┌─────────────────────────────────────┐
        └──────────│          COLLECT_TRASH              │
  after collect    │  Stop motors, fire actuator          │
                   └─────────────────────────────────────┘
```

### Priority table

| Priority | State | Pre-empts |
|----------|-------|-----------|
| 1 (highest) | AVOID_OBSTACLE | Everything |
| 2 | COLLECT_TRASH | TRACK_TRASH, NAVIGATE |
| 3 | TRACK_TRASH | NAVIGATE |
| 4 | RETURN_TO_PATH | NAVIGATE |
| 5 (lowest) | NAVIGATE | — |

Only one state controls motors at any time.  No override chains.

---

## Visual Servoing (Trash Collection)

```
Camera frame (640×640):
┌────────────────────────────────────────────┐
│                                            │
│              ┌───────┐                     │
│              │ TRASH │  bbox               │
│              └───────┘                     │
│          cx ─┘                             │
│                                            │
│                   │                        │
│               frame_centre_x = 320        │
└────────────────────────────────────────────┘

error_x = (bbox.center_x - 320) / 320       → normalised [-1, +1]

omega_cmd = TRASH_STEERING_KP × error_x × 90
          = 0.10 × 0.30 × 90 = 2.7 deg/s   (gentle right turn)

Collection trigger:
  area_frac = (bbox.w × bbox.h) / (640 × 640)
  if area_frac >= 0.15  →  COLLECT_TRASH
```

---

## Obstacle Avoidance

```
Repulsion vector (image space):

  obstacle left of centre (cx_norm < 0)  →  steer RIGHT (omega > 0)
  obstacle right of centre (cx_norm > 0) →  steer LEFT  (omega < 0)

  omega_avoid = -GAIN × cx_norm

  GAIN = 25 deg/s  →  obstacle fully on right (cx_norm=1.0) → 25 deg/s left turn

Speed reduction:
  proximity = min(width_frac / STOP_WIDTH_FRAC, 1.0)
  speed = pp_speed × SPEED_FACTOR × (1 - proximity × 0.5)

Hard stop:
  width_frac >= 0.35  →  both motors off, blinking indicator

No SLAM, no RRT, no DWA.  Pure reactive steering suitable for Pi 5.
```

---

## Detection Queue Protocol

```python
# HailoRunner (OS thread) → puts:
det = RawDetection(
    class_id=0,          # 0=obstacle, 1=trash
    confidence=0.81,
    bbox=BBox(x=40, y=100, w=200, h=300),
    frame_w=640,
    frame_h=640,
)
queue.put_nowait(det)   # drops oldest if full

# PerceptionManager (asyncio) → gets:
det = queue.get_nowait()   # non-blocking, called in _tick_auto
```

Queue maxsize = 2 (configurable via `HailoConfig.QUEUE_MAXSIZE`).
Oldest frames dropped when full — ensures navigation always sees freshest detections.

---

## Return-to-Path Recovery

After any excursion (trash collection or obstacle avoidance):

1. `BehaviorManager._transition(RETURN_TO_PATH)` calls `engine._reanchor(lat, lng, from_index)`.
2. `_reanchor` searches forward `REANCHOR_SEARCH_WINDOW` path points for the nearest one.
3. `sequencer.current_index` is updated to the nearest point.
4. Next tick: PurePursuitController steers toward the re-anchored target.
5. `BehaviorManager` transitions to `NAVIGATE` on next execution.

This reuses the same `_reanchor()` method used by the existing manual-override exit path.

---

## Configuration Reference

All parameters in `config.py` — runtime-configurable, no redeployment needed:

### Perception
| Parameter | Default | Description |
|-----------|---------|-------------|
| `TRASH_CONFIDENCE_THRESHOLD` | 0.45 | Min confidence to track trash |
| `OBSTACLE_CONFIDENCE_THRESHOLD` | 0.40 | Min confidence to track obstacle |
| `MIN_CONFIRM_FRAMES` | 2 | Frames before observation is confirmed |
| `MAX_MISS_FRAMES` | 5 | Frames before observation is cleared |
| `MIN_BBOX_AREA_PX2` | 400 | Minimum bbox area (pixels²) |

### Trash
| Parameter | Default | Description |
|-----------|---------|-------------|
| `TRASH_STEERING_KP` | 0.10 | Visual servoing proportional gain |
| `TRASH_APPROACH_SPEED` | 0.6 m/s | Speed during approach |
| `COLLECTION_AREA_FRAC` | 0.15 | Collection trigger: bbox area / frame area |
| `CENTRE_DEADBAND_PX` | 20 px | Steering suppressed within this error |
| `COLLECTION_TIMEOUT_S` | 15 s | Abort approach after this duration |
| `MAX_DEVIATIONS_PER_LANE` | 3 | Max trash deviations per coverage lane |

### Obstacle
| Parameter | Default | Description |
|-----------|---------|-------------|
| `OBSTACLE_REPULSION_GAIN` | 25 deg/s | Avoidance steering gain |
| `OBSTACLE_WIDTH_FRAC_THRESHOLD` | 0.10 | Min width fraction to trigger avoidance |
| `OBSTACLE_SPEED_FACTOR` | 0.4 | Speed reduction during avoidance |
| `OBSTACLE_CLEAR_FRAMES` | 8 | Frames clear before exiting avoidance |
| `OBSTACLE_STOP_WIDTH_FRAC` | 0.35 | Hard-stop threshold |

---

## Integration with Existing Code

### engine.py changes (surgical — 4 additions)

```python
# __init__: create perception/behavior stack
self._detection_queue = queue.Queue(maxsize=hailo_cfg().QUEUE_MAXSIZE)
self.perception  = PerceptionManager(self._detection_queue)
self.behavior    = BehaviorManager(reanchor_fn=self._reanchor)
self._hailo      = HailoRunner(self._detection_queue)

# run(): start Hailo thread alongside navigation loop
self._hailo.start()   # ← added
# ... existing loop unchanged ...
self._hailo.stop()    # ← added at shutdown

# _tick_auto(): two insertions between existing steps 2 and 3
self.perception.update()   # ← drain queue (non-blocking)
# ... existing pure-pursuit unchanged ...
beh_cmd = self.behavior.arbitrate(...)   # ← insert before motor write
omega_cmd = beh_cmd.omega_cmd
speed_cmd = beh_cmd.speed_cmd
# ... rest of tick unchanged ...
```

### What is NOT changed

- `_tick_manual()` — manual mode is completely unmodified.
- Sequencer advancement logic — unchanged.
- Pure-pursuit controller — unchanged.
- Motor writer protocol — unchanged.
- WebSocket frame structure — additive only (new `behavior_state` and `perception` fields).
- RC monitor — unchanged.
- All REST endpoints — unchanged.
- Telemetry sources (Mock + UART) — unchanged.

---

## Switching to Real Hardware

### Enable Hailo-8L

```python
# config.py
_hailo = HailoConfig(
    MOCK_MODE=False,
    hef_path="models/scrub_detector.hef",
    MAX_FPS=10,
)
```

### Enable UART telemetry

```python
# main.py  (unchanged from original)
from telemetry.source import UartTelemetrySource
telemetry_source = UartTelemetrySource()
```

### Enable motor output

```python
# motor_writer.py
MOCK_MODE = False
```

---

## Validation Without Hardware (Mock Mode)

```bash
# Start backend in mock mode (default)
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000

# Inject a mock obstacle via REST
curl -X POST http://localhost:8000/api/debug/inject_obstacle \
  -H 'Content-Type: application/json' \
  -d '{"cx_norm": 0.3, "width_frac": 0.22}'

# Inject mock trash
curl -X POST http://localhost:8000/api/debug/inject_trash \
  -H 'Content-Type: application/json' \
  -d '{"cx_norm": 0.05, "area_frac": 0.07}'

# Clear mock injections
curl -X POST http://localhost:8000/api/debug/clear_mock

# Check perception/behavior status
curl http://localhost:8000/api/debug/perception
```

Watch the WebSocket telemetry stream: `behavior_state` will transition between
`navigate`, `track_trash`, `collect_trash`, `avoid_obstacle`, and `return_to_path`
in response to injected detections.  The existing dashboard map and motor
indicators update in real time.
