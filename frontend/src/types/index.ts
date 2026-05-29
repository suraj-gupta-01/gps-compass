export interface LatLng { lat: number; lng: number; }
export interface GroundControl { id: string; position: LatLng; }

export interface PlannerPoint { lat: number; lng: number; }
export interface PlannerWaypointPathSegment {
  type: 'waypoint_path'; id: string; label: string; points: PlannerPoint[];
}
export interface PlannerAreaCoverageSegment {
  type: 'area_coverage'; id: string; label: string;
  sweepWidth: number; polygon: PlannerPoint[];
}
export type PlannerSegment = PlannerWaypointPathSegment | PlannerAreaCoverageSegment;
export interface PlannerMissionJSON {
  groundControl: GroundControl | null;
  mission: PlannerSegment[];
}

export type OperatingMode = 'auto' | 'manual';
export type NavState =
  | 'idle' | 'navigating' | 'coverage' | 'returning'
  | 'completed' | 'paused' | 'manual';
export type SteeringCmd = 'forward' | 'turn_left' | 'turn_right' | 'stop' | 'large_correction';
export interface MotorState { left: boolean; right: boolean; blinking: boolean; }

export interface TelemetryFrame {
  lat: number; lng: number;
  heading: number;
  speed: number;
  target_lat: number; target_lng: number;
  lookahead_lat: number; lookahead_lng: number;
  required_heading: number;
  heading_error: number;
  distance_to_target: number;
  distance_to_lookahead: number;
  omega: number;
  nav_state: NavState;
  active_segment_label: string;
  active_segment_index: number;
  total_path_points: number;
  mission_progress: number;
  gps_accepted: boolean;
  steering: SteeringCmd;
  motor: MotorState;
  mode: OperatingMode;
  mode_since: number;
  source: 'mock' | 'uart';
  timestamp: number;
}

export interface MissionUploadResponse {
  ok: boolean; total_points: number;
  segments: number; message: string;
  generated_path: { lat: number; lng: number }[];
}

export type ConnectionStatus = 'disconnected' | 'connecting' | 'connected' | 'error';
export type MapLayer = 'street' | 'satellite';

export interface ManualMotorCmd {
  left: boolean; right: boolean; throttle: number;
}

export interface DashboardStore {
  wsStatus: ConnectionStatus;
  backendUrl: string;
  missionLoaded: boolean;
  missionJSON: PlannerMissionJSON | null;
  generatedPath: LatLng[];
  trail: LatLng[];
  telemetry: TelemetryFrame | null;
  mapLayer: MapLayer;
  followVessel: boolean;
  // Manual mode UI state (mirrors what we last sent to backend)
  manualLeft: boolean;
  manualRight: boolean;
  manualThrottle: number;
  setBackendUrl: (url: string) => void;
  setWsStatus: (s: ConnectionStatus) => void;
  loadMissionJSON: (json: PlannerMissionJSON) => void;
  setGeneratedPath: (path: LatLng[]) => void;
  applyTelemetry: (frame: TelemetryFrame) => void;
  clearMission: () => void;
  setMapLayer: (l: MapLayer) => void;
  setFollowVessel: (v: boolean) => void;
  setManualMotor: (left: boolean, right: boolean, throttle?: number) => void;
}