"""
config.py — Centralised runtime configuration for the AeroNav autonomy stack.

All tuning knobs live here.  Importing modules read from this singleton so
a runtime config-reload (future feature) only needs to touch one object.

Sections
────────
  PerceptionConfig   — detection thresholds, filters
  TrashConfig        — visual servoing, collection geometry
  ObstacleConfig     — reactive avoidance gains
  BehaviorConfig     — state-machine timeouts, hysteresis
  HailoConfig        — inference thread settings
  VesselConfig       — kinematics / navigation (already used by engine)

Every numeric constant that previously lived inside a function body or as a
module-level magic number has been migrated here.
"""

from dataclasses import dataclass, field


# ══════════════════════════════════════════════════════════════════════════════
# Perception
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PerceptionConfig:
    # Hailo model
    hef_path: str          = "models/scrub_detector.hef"
    input_width:  int      = 640
    input_height: int      = 640

    # Class IDs (must match HEF training labels)
    CLASS_OBSTACLE: int    = 0
    CLASS_TRASH:    int    = 1

    # Confidence thresholds — detections below these are silently dropped
    TRASH_CONFIDENCE_THRESHOLD:    float = 0.45
    OBSTACLE_CONFIDENCE_THRESHOLD: float = 0.40

    # Temporal smoothing: minimum consecutive frames a detection must appear
    # before it graduates from candidate → confirmed observation
    MIN_CONFIRM_FRAMES: int = 2   # ~200 ms at 10 Hz

    # Maximum frames a confirmed detection is held without a matching update
    MAX_MISS_FRAMES:    int = 5   # ~500 ms hold

    # Minimum bounding-box area (px²) to avoid tiny phantom detections
    MIN_BBOX_AREA_PX2:  float = 400.0

    # Maximum simultaneous tracked obstacles / trash items
    MAX_TRACKED_OBSTACLES: int = 8
    MAX_TRACKED_TRASH:     int = 4

    # Debug / mock injection
    MOCK_DETECTIONS:   bool  = False   # inject synthetic detections in mock mode
    DEBUG_LOG_DETS:    bool  = False   # log every raw detection to structlog


# ══════════════════════════════════════════════════════════════════════════════
# Trash collection (visual servoing)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class TrashConfig:
    # Proportional gain: steering_omega = KP * pixel_error_x
    # Units: (deg/s) per pixel.  Tune until robot centres on target
    # without oscillation.  Typical range 0.05–0.20 for a ~640px frame.
    TRASH_STEERING_KP:    float = 0.10

    # Approach speed while visually tracking trash (m/s)
    TRASH_APPROACH_SPEED: float = 0.6

    # Collection trigger: fraction of frame area occupied by trash bbox.
    # When bbox_area / (frame_w * frame_h) >= threshold → declare collected.
    # 0.15 ≈ trash fills ~40% of frame width at typical collection range.
    COLLECTION_AREA_FRAC: float = 0.15

    # Pixel-space dead-band: heading corrections smaller than this (px) are
    # suppressed to avoid oscillation when nearly centred.
    CENTRE_DEADBAND_PX:   float = 20.0

    # Maximum time allowed for a single trash collection attempt (seconds).
    # If exceeded the robot gives up and returns to path.
    COLLECTION_TIMEOUT_S: float = 15.0

    # Maximum lateral deviation from coverage path allowed during approach (m).
    # Guards against chasing trash into hazardous terrain.
    MAX_LATERAL_DEVIATION_M: float = 8.0

    # Maximum number of trash deviations per coverage lane before the robot
    # stops diverting and continues the lane uninterrupted.
    MAX_DEVIATIONS_PER_LANE: int  = 3


# ══════════════════════════════════════════════════════════════════════════════
# Obstacle avoidance (reactive, potential-field style)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ObstacleConfig:
    # Repulsion gain: higher → stronger swerve away from obstacle.
    # omega_avoid = GAIN * (frame_centre_x - obstacle_centre_x) / frame_w
    # Negative because obstacle on left → steer right.
    OBSTACLE_REPULSION_GAIN: float = 25.0   # deg/s per normalised unit

    # Obstacle must occupy at least this fraction of frame width to trigger
    # avoidance.  Filters out small/distant obstacles that are non-threatening.
    OBSTACLE_WIDTH_FRAC_THRESHOLD: float = 0.10

    # Speed reduction factor applied during active obstacle avoidance (0–1).
    OBSTACLE_SPEED_FACTOR: float = 0.4

    # Number of consecutive frames obstacle must be absent before avoidance ends.
    OBSTACLE_CLEAR_FRAMES:  int  = 8   # ~800 ms of clear frames

    # Hard-stop threshold: if obstacle fills more than this fraction of frame
    # width the robot stops completely until the obstacle clears.
    OBSTACLE_STOP_WIDTH_FRAC: float = 0.35


# ══════════════════════════════════════════════════════════════════════════════
# Behavior arbitration
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class BehaviorConfig:
    # Minimum time a state must be active before it can be pre-empted by the
    # same-priority state (prevents rapid oscillation).
    MIN_STATE_DWELL_S: float = 0.5

    # After avoidance ends, robot must be within this radius (m) of return
    # waypoint before NAVIGATE is considered re-engaged.
    RETURN_TO_PATH_THRESHOLD_M: float = 3.0

    # Re-anchor search window (path points) after trash or obstacle excursion.
    REANCHOR_WINDOW: int = 30

    # Tick rate of the behavior manager (Hz).  Matches navigation engine.
    TICK_HZ: float = 10.0


# ══════════════════════════════════════════════════════════════════════════════
# Hailo inference thread
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class HailoConfig:
    # Max inference frames per second.  Hailo-8L can sustain ~30 FPS on YOLO-class
    # models; we cap at 10 to match the navigation tick rate and save CPU.
    MAX_FPS:            int  = 10

    # Detection queue max depth (frames).  Old frames are dropped when full.
    QUEUE_MAXSIZE:      int  = 2

    # Whether to run in mock/CPU mode (no Hailo hardware required)
    MOCK_MODE:          bool = True

    # Mock detection injection interval (seconds) — used in debug mode
    MOCK_INJECT_INTERVAL_S: float = 2.0


# ══════════════════════════════════════════════════════════════════════════════
# Singleton accessors
# ══════════════════════════════════════════════════════════════════════════════

_perception = PerceptionConfig()
_trash      = TrashConfig()
_obstacle   = ObstacleConfig()
_behavior   = BehaviorConfig()
_hailo      = HailoConfig()


def perception_cfg() -> PerceptionConfig: return _perception
def trash_cfg()      -> TrashConfig:      return _trash
def obstacle_cfg()   -> ObstacleConfig:   return _obstacle
def behavior_cfg()   -> BehaviorConfig:   return _behavior
def hailo_cfg()      -> HailoConfig:      return _hailo
