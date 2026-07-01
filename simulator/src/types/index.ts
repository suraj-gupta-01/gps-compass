// ── Simulator type definitions ──────────────────────────────────────────────

// What it does: central shared TypeScript contracts for AeroSim state,
// telemetry, placed objects, and HIL mode.
// Imports from: none.
// Behavior: type-only; additive HIL fields do not change runtime behavior.

export interface LatLng { lat: number; lng: number; }

export interface VesselConfig {
  cruise_speed_mps: number;
  max_speed_mps: number;
  speed_tau: number;
  max_turn_rate_dps: number;
  turn_speed_factor: number;
  lookahead_m: number;
  waypoint_radius_m: number;
  heading_kp: number;
  length_m: number;
  width_m: number;
}

export interface CameraConfig {
  hfov_deg: number;
  max_range_m: number;
  frame_w: number;
  frame_h: number;
}

export interface PathPoint {
  lat: number;
  lng: number;
  segment_label: string;
  segment_type: string;
  acceptance_radius_m: number;
}

export type BehaviorStateName =
  | 'navigate'
  | 'track_trash'
  | 'collect_trash'
  | 'avoid_obstacle'
  | 'return_to_path';

export interface SimObstacle {
  id: string;
  lat: number;
  lng: number;
  radius_m: number;
  type: 'obstacle';
}

export interface SimTrash {
  id: string;
  lat: number;
  lng: number;
  area_cm2: number;
  type: 'trash';
}

export interface SimObstacleObs {
  image_cx_norm: number;
  width_frac: number;
  confidence: number;
}

export interface SimTrashObs {
  image_cx_norm: number;
  image_cy_norm: number;
  area_frac: number;
  confidence: number;
}

export interface SimMotorCmd {
  omega_cmd: number;
  speed_cmd: number;
  left_on: boolean;
  right_on: boolean;
  blinking: boolean;
  steering_label: string;
}

export interface SimDebugState {
  tick: number;
  behavior_state: BehaviorStateName;
  omega_cmd: number;
  speed_cmd: number;
  lat: number;
  lng: number;
  heading: number;
  speed: number;
  path_index: number;
  total_path_points: number;
  lookahead_lat: number;
  lookahead_lng: number;
  obstacle_obs: SimObstacleObs | null;
  trash_obs: SimTrashObs | null;
  motor_cmd: SimMotorCmd;
  nav_state: string;
  gps_accepted: boolean;
}

export interface BackendTelemetryFrame {
  lat: number;
  lng: number;
  heading: number;
  speed: number;
  target_lat: number;
  target_lng: number;
  lookahead_lat: number;
  lookahead_lng: number;
  required_heading: number;
  heading_error: number;
  distance_to_target: number;
  distance_to_lookahead: number;
  omega: number;
  omega_cmd?: number;
  speed_cmd?: number;
  nav_state: string;
  active_segment_label: string;
  active_segment_index: number;
  total_path_points: number;
  mission_progress: number;
  gps_accepted: boolean;
  steering: string;
  motor: { left: boolean; right: boolean; blinking: boolean };
  motor_cmd?: BackendMotorCmd;
  mode: string;
  mode_since: number;
  source: string;
  timestamp: number;
  behavior_state?: string;
  perception?: Record<string, unknown>;
}

export interface BackendMotorCmd {
  left_us: number;
  right_us: number;
  omega_cmd: number;
  speed_cmd: number;
  steering_label: string;
  behavior_state: string;
}

export interface SimStatusResponse {
  source_type: 'mock' | 'uart';
  motor_mock: boolean;
  perception_mock_active: boolean;
  engine_running: boolean;
  engine_paused: boolean;
  mission_loaded: boolean;
  nav_state: string;
}

export interface HilFeedStats {
  telemetry_send_hz: number;
  perception_send_hz: number;
  ws_recv_hz: number;
  last_error: string | null;
}

export type ToolbarMode = 'none' | 'place_obstacle' | 'place_trash' | 'place_gcs' | 'place_waypoint' | 'draw_geofence';
