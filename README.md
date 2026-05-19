# AeroNav — Mission Execution Dashboard

Companion app to the Mission Planner. Import a mission JSON, connect to the Python
backend, and watch the vessel execute the full mission in real time.

---

## Quick Start

### 1 — Backend (Python / FastAPI)

```bash
cd mission-execution
pip install -r requirements.txt
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 2 — Frontend (React / Vite)

```bash
cd mission-execution/frontend
npm install
npm run dev
# → http://localhost:5173
```

---

## Using the Dashboard

1. Open the dashboard in your browser.
2. **Load Mission JSON** — click "Load Mission JSON" in the left panel and select the
   `.json` file exported from the Mission Planner.  
   The backend receives the mission, generates the lawnmower sweep paths for any
   coverage zones, and returns the full flat path.
3. **Start Mission** — click "Start Mission". The vessel (mock or real) begins
   navigating the path.
4. Watch the map: green vessel icon moves, amber target crosshair shows the current
   waypoint, purple dashed lines show the coverage sweep pattern.
5. **Pause / Resume / Reset** as needed.

---

## Switching to Real UART Hardware (Raspberry Pi 5)

In `backend/main.py`, replace:

```python
from telemetry.source import MockTelemetrySource
telemetry_source = MockTelemetrySource()
```

with:

```python
from telemetry.source import UartTelemetrySource
telemetry_source = UartTelemetrySource()
```

Configure the port/baud in `backend/telemetry/source.py`:

```python
SERIAL_PORT = '/dev/ttyAMA0'   # or /dev/ttyUSB0
BAUD_RATE   = 115200
```

Expected serial format (one line per update):
```
LAT,LNG,HEADING\n
37.774900,-122.419400,045.3\n
```

---

## Architecture

```
mission-execution/
├── requirements.txt
├── frontend/                   # React + Vite + TypeScript
│   └── src/
│       ├── types/index.ts      # All TypeScript interfaces
│       ├── store/              # Zustand dashboard store
│       ├── services/           # WebSocket + REST client
│       ├── hooks/              # useWebSocket lifecycle hook
│       ├── utils/              # geo formatting, Leaflet icons
│       └── components/
│           ├── MissionMap      # Leaflet map with all overlays
│           ├── MissionControl  # Left panel: load, start/stop
│           ├── TelemetryPanel  # Right panel: live data cards
│           ├── CompassWidget   # SVG compass rose + error bar
│           └── MotorIndicator  # LED indicators + steering label
└── backend/
    ├── main.py                 # FastAPI app + lifespan
    ├── api/routes.py           # REST + WebSocket endpoints
    ├── navigation/engine.py    # 10 Hz execution loop
    ├── mission/
    │   ├── models.py           # Pydantic models (mirror planner JSON)
    │   └── sequencer.py        # Flat path builder + waypoint tracking
    ├── coverage/lawnmower.py   # Boustrophedon sweep generator
    ├── telemetry/source.py     # Mock + UART source abstraction
    └── utils/geo.py            # Haversine, bearing, heading error
```

### Navigation engine loop (10 Hz)

```
read telemetry (mock or UART)
  ↓
sequencer.update(lat, lng)      ← advance index if within 3m of target
  ↓
calc_bearing(pos → target)      → required_heading
calc_heading_error(hdg, req)    → signed error °
  ↓
steering decision:
  |error| ≤  8° → forward       (both motors ON)
  |error| ≤ 20° → gentle turn   (one motor ON)
  |error| > 20° → hard turn     (one motor ON)
  |error| > 90° → large_correction (both motors blinking)
  ↓
broadcast TelemetryFrame → WebSocket → frontend
```

### Segment types handled

| Segment        | Path generation             |
|----------------|-----------------------------|
| waypoint_path  | Points visited in order     |
| area_coverage  | Boustrophedon lawnmower sweep generated from polygon + sweepWidth |

---

## Future Extension Points

| Feature            | Where to add                                      |
|--------------------|---------------------------------------------------|
| MAVLink            | New `telemetry/mavlink_source.py`                 |
| ROS 2              | New `telemetry/ros2_source.py`                    |
| Real motor control | New `navigation/motor_driver.py` called by engine |
| Obstacle avoidance | Hook into `sequencer.update()` to insert detour   |
| YOLO detections    | WebSocket message type `detection_frame`          |
| Multi-vessel       | Engine pool keyed by vessel ID                    |
| Autonomous replan  | Replace sequencer path on-the-fly via `/api/replan` |
