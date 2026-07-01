// SimEngine — pure-TS simulation engine with tick()-based loop.
// No React dependency; framework-agnostic so it can be unit tested independently.
// Wires together: kinematics, pure-pursuit, behavior, perception, FOV detection.
// What it does: pure-TS AeroSim plant/controller loop for standalone mode and
// HIL plant integration when backend motor commands are supplied.
// Imports from: simulator types, kinematics, controller, behavior, perception,
// filters, and geo helpers.
// Behavior: preserves standalone control; HIL is additive and uses backend
// omega/speed for kinematics while keeping the TS controller as a local estimate.

import type {
  VesselConfig, CameraConfig, PathPoint, SimObstacle, SimTrash,
  SimDebugState, SimObstacleObs, SimTrashObs, BehaviorStateName,
} from '../types';
import { resetVesselState, stepKinematics, type VesselState } from './kinematics';
import { findLookahead, computeOmega, computeSpeed, steeringFromOmega } from './controller';
import { arbitrate, newBehaviorManager, type BehaviorManagerState } from './behavior';
import { PerceptionManager, isInFOV, worldToFrame, TRASH_CFG, PERCEPTION_CFG } from './perception';
import { CompassFilter, GpsFilter } from './filters';
import { haversine, METERS_PER_DEG_LAT, metersPerDegLng, bearing as calc_bearing } from './geo';

export const TICK_INTERVAL = 0.1; // 10 Hz

export interface SimEngineState {
  // Vessel
  vessel: VesselState;
  // Path
  path: PathPoint[];
  pathIndex: number;
  loaded: boolean;
  // Perception objects
  obstacles: SimObstacle[];
  trash: SimTrash[];
  // Subsystems
  behavior: BehaviorManagerState;
  perception: PerceptionManager;
  compassFilter: CompassFilter;
  gpsFilter: GpsFilter;
  // Telemetry
  lastLat: number;
  lastLng: number;
  lastHeading: number;
  // Control
  running: boolean;
  paused: boolean;
  tickCount: number;
  // Camera
  camera: CameraConfig;
  // Last lookahead for display
  lookaheadLat: number;
  lookaheadLng: number;
  // Last motor cmd for display
  lastMotorCmd: { omega_cmd: number; speed_cmd: number; left_on: boolean; right_on: boolean; blinking: boolean; steering_label: string };
  lastBackendMotorCmd: BackendMotorCmd | null;
}

export interface BackendMotorCmd {
  omega_cmd: number;
  speed_cmd: number;
  left_us?: number;
  right_us?: number;
  steering_label?: string;
  behavior_state?: string;
}

export interface SyntheticPerceptionFrame {
  obstacles: { cx_norm: number; width_frac: number; confidence: number }[];
  trash: { cx_norm: number; cy_norm: number; area_frac: number; confidence: number }[];
}

export function newSimEngine(vesselCfg: VesselConfig, camera: CameraConfig): SimEngineState {
  return {
    vessel: resetVesselState(37.7749, -122.4194),
    path: [],
    pathIndex: 0,
    loaded: false,
    obstacles: [],
    trash: [],
    behavior: newBehaviorManager(),
    perception: new PerceptionManager(),
    compassFilter: new CompassFilter(0.3),
    gpsFilter: new GpsFilter(25.0),
    lastLat: 37.7749,
    lastLng: -122.4194,
    lastHeading: 0,
    running: false,
    paused: true,
    tickCount: 0,
    camera,
    lookaheadLat: 37.7749,
    lookaheadLng: -122.4194,
    lastMotorCmd: { omega_cmd: 0, speed_cmd: 0, left_on: false, right_on: false, blinking: false, steering_label: 'stop' },
    lastBackendMotorCmd: null,
  };
}

export function loadPath(engine: SimEngineState, path: PathPoint[]): void {
  engine.path = path;
  engine.pathIndex = 0;
  engine.loaded = path.length > 0;
  engine.paused = true;
  engine.behavior = newBehaviorManager();
  engine.perception.reset();
  if (path.length > 0) {
    // Place vessel slightly behind the first waypoint (1 m) so it must move
    const origin = path[0];
    if (path.length > 1) {
      const next = path[1];
      const b = calc_bearing(origin.lat, origin.lng, next.lat, next.lng) * Math.PI / 180.0;
      const offsetM = 1.0; // 1 meter behind
      const dx = -offsetM * Math.sin(b);
      const dy = -offsetM * Math.cos(b);
      const lat = origin.lat + dy / METERS_PER_DEG_LAT;
      const lng = origin.lng + dx / metersPerDegLng(origin.lat);
      engine.vessel = resetVesselState(lat, lng, 0);
      engine.lastLat = lat;
      engine.lastLng = lng;
      engine.lastHeading = 0;
    } else {
      engine.vessel = resetVesselState(origin.lat, origin.lng, 0);
      engine.lastLat = origin.lat;
      engine.lastLng = origin.lng;
      engine.lastHeading = 0;
    }
  }
}

export function startSim(engine: SimEngineState): void {
  if (!engine.loaded) return;
  engine.running = true;
  engine.paused = false;
}

export function pauseSim(engine: SimEngineState): void {
  engine.paused = true;
}

export function resumeSim(engine: SimEngineState): void {
  if (!engine.loaded) return;
  engine.paused = false;
}

export function resetSim(engine: SimEngineState): void {
  engine.paused = true;
  engine.behavior = newBehaviorManager();
  engine.perception.reset();
  engine.tickCount = 0;
  if (engine.path.length > 0) {
    // Reset vessel slightly behind first point to require forward motion
    const origin = engine.path[0];
    if (engine.path.length > 1) {
      const next = engine.path[1];
      const b = calc_bearing(origin.lat, origin.lng, next.lat, next.lng) * Math.PI / 180.0;
      const offsetM = 1.0;
      const dx = -offsetM * Math.sin(b);
      const dy = -offsetM * Math.cos(b);
      const lat = origin.lat + dy / METERS_PER_DEG_LAT;
      const lng = origin.lng + dx / metersPerDegLng(origin.lat);
      engine.vessel = resetVesselState(lat, lng, 0);
      engine.lastLat = lat;
      engine.lastLng = lng;
      engine.lastHeading = 0;
    } else {
      engine.vessel = resetVesselState(origin.lat, origin.lng, 0);
      engine.lastLat = origin.lat;
      engine.lastLng = origin.lng;
      engine.lastHeading = 0;
    }
    engine.pathIndex = 0;
  }
}

export function collectSyntheticPerception(engine: SimEngineState): SyntheticPerceptionFrame {
  const v = engine.vessel;
  // Port accuracy: match backend PerceptionManager.update() ordering by
  // resetting transients before this tick's synthetic FOV detections are fed.
  engine.perception.resetTick();
  const frame: SyntheticPerceptionFrame = { obstacles: [], trash: [] };

  for (const obs of engine.obstacles) {
    const fov = isInFOV(v.lat, v.lng, v.heading, obs.lat, obs.lng, engine.camera.hfov_deg, engine.camera.max_range_m);
    if (fov) {
      // Port accuracy: mirrors backend PerceptionManager._build_obstacle_obs()
      // normalized center/width fields after the simulator projects world geometry.
      const bbox = worldToFrame(fov.bearingOffset, fov.distance, obs.radius_m, engine.camera.frame_w, engine.camera.frame_h, engine.camera.hfov_deg);
      frame.obstacles.push({
        cx_norm: (bbox.cx - engine.camera.frame_w / 2) / (engine.camera.frame_w / 2),
        width_frac: bbox.w / engine.camera.frame_w,
        confidence: 0.85,
      });
    }
  }

  for (const t of engine.trash) {
    const fov = isInFOV(v.lat, v.lng, v.heading, t.lat, t.lng, engine.camera.hfov_deg, engine.camera.max_range_m);
    if (fov) {
      const radiusM = Math.sqrt(t.area_cm2 / Math.PI) / 100;
      const bbox = worldToFrame(fov.bearingOffset, fov.distance, radiusM, engine.camera.frame_w, engine.camera.frame_h, engine.camera.hfov_deg);
      frame.trash.push({
        cx_norm: (bbox.cx - engine.camera.frame_w / 2) / (engine.camera.frame_w / 2),
        cy_norm: (bbox.cy - engine.camera.frame_h / 2) / (engine.camera.frame_h / 2),
        area_frac: (bbox.w * bbox.h) / (engine.camera.frame_w * engine.camera.frame_h),
        confidence: 0.80,
      });
    }
  }

  return frame;
}

function feedSyntheticPerception(engine: SimEngineState, frame: SyntheticPerceptionFrame): void {
  engine.perception.resetTick();
  for (const obs of frame.obstacles) {
    engine.perception.feedObstacleDet(obs.cx_norm, obs.width_frac, obs.confidence);
  }
  for (const t of frame.trash) {
    engine.perception.feedTrashDet(t.cx_norm, t.cy_norm, t.area_frac, t.confidence);
  }
  engine.perception.finalizeTick();
}

/**
 * One simulation tick. Advances the vessel, runs perception, behavior, kinematics.
 * Pass the same vesselCfg each time so tuning is live.
 */
export function tick(engine: SimEngineState, vesselCfg: VesselConfig, dt: number, backendMotorCmd?: BackendMotorCmd | null): void {
  if (engine.paused || !engine.loaded || engine.path.length === 0) return;

  const now = engine.tickCount * TICK_INTERVAL;
  engine.tickCount++;

  const v = engine.vessel;

  // ── 1. FOV-based perception detection ─────────────────────────────────────
  // Check all obstacles/trash against camera FOV
  for (const obs of engine.obstacles) {
    const fov = isInFOV(v.lat, v.lng, v.heading, obs.lat, obs.lng, engine.camera.hfov_deg, engine.camera.max_range_m);
    if (fov) {
      const frame = worldToFrame(fov.bearingOffset, fov.distance, obs.radius_m, engine.camera.frame_w, engine.camera.frame_h, engine.camera.hfov_deg);
      const cxNorm = (frame.cx - engine.camera.frame_w / 2) / (engine.camera.frame_w / 2);
      const wFrac = frame.w / engine.camera.frame_w;
      engine.perception.feedObstacleDet(cxNorm, wFrac, 0.85);
    }
  }

  for (const t of engine.trash) {
    const fov = isInFOV(v.lat, v.lng, v.heading, t.lat, t.lng, engine.camera.hfov_deg, engine.camera.max_range_m);
    if (fov) {
      const radiusM = Math.sqrt(t.area_cm2 / Math.PI) / 100; // cm² to m radius
      const frame = worldToFrame(fov.bearingOffset, fov.distance, radiusM, engine.camera.frame_w, engine.camera.frame_h, engine.camera.hfov_deg);
      const cxNorm = (frame.cx - engine.camera.frame_w / 2) / (engine.camera.frame_w / 2);
      const cyNorm = (frame.cy - engine.camera.frame_h / 2) / (engine.camera.frame_h / 2);
      const areaFrac = (frame.w * frame.h) / (engine.camera.frame_w * engine.camera.frame_h);
      engine.perception.feedTrashDet(cxNorm, cyNorm, areaFrac, 0.80);
    }
  }

  // ── 2. Perception update (temporal confirm/decay) ──────────────────────────
  engine.perception.update();

  // ── 3. Pure-pursuit ────────────────────────────────────────────────────────
  const la = findLookahead(v.lat, v.lng, engine.path, engine.pathIndex, vesselCfg.lookahead_m);
  // Port accuracy: matches MissionSequencer.advance_to_index() semantics by
  // accepting the lookahead index directly instead of only one point per tick.
  engine.pathIndex = Math.max(engine.pathIndex, Math.min(la.index, engine.path.length - 1));

  // Advance if within acceptance radius (fallback)
  if (engine.pathIndex < engine.path.length) {
    const target = engine.path[engine.pathIndex];
    const dist = haversine(v.lat, v.lng, target.lat, target.lng);
    if (dist <= target.acceptance_radius_m) {
      engine.pathIndex = Math.min(engine.pathIndex + 1, engine.path.length);
    }
  }

  engine.lookaheadLat = la.lat;
  engine.lookaheadLng = la.lng;

  if (engine.pathIndex >= engine.path.length) {
    engine.paused = true;
    return;
  }

  const target = engine.path[Math.min(engine.pathIndex, engine.path.length - 1)];

  const ppOmega = computeOmega(v.lat, v.lng, v.heading, la.lat, la.lng, vesselCfg.heading_kp);
  const reqHdg = ((Math.atan2((la.lng - v.lng) * Math.cos((v.lat * Math.PI) / 180), (la.lat - v.lat)) * 180 / Math.PI) + 360) % 360;
  const err = ((reqHdg - v.heading + 180) % 360) - 180;
  const ppSpeed = computeSpeed(err, vesselCfg.cruise_speed_mps, vesselCfg.turn_speed_factor);

  // ── 4. Behavior arbitration ────────────────────────────────────────────────
  const obs = engine.perception.obstacle;
  const trash = engine.perception.trash;
  const collectionTriggered = trash !== null && trash.area_frac >= TRASH_CFG.COLLECTION_AREA_FRAC;

  const behCmd = arbitrate(
    engine.behavior, obs, trash, ppOmega, ppSpeed,
    engine.pathIndex, target.segment_label, now, collectionTriggered,
  );

  engine.lastMotorCmd = behCmd;

  // ── 5. Kinematics ─────────────────────────────────────────────────────────
  // Port accuracy: stepKinematics mirrors backend/navigation/kinematics.py
  // VesselKinematics.step(). HIL supplies backend commands for the plant.
  engine.lastBackendMotorCmd = backendMotorCmd ?? null;
  const plantOmega = backendMotorCmd?.omega_cmd ?? behCmd.omega_cmd;
  const plantSpeed = backendMotorCmd?.speed_cmd ?? behCmd.speed_cmd;
  engine.vessel = stepKinematics(v, vesselCfg, plantOmega, dt, plantSpeed);

  // ── 6. Synthetic GPS/compass filtering ─────────────────────────────────────
  const gpsResult = engine.gpsFilter.update(engine.vessel.lat, engine.vessel.lng);
  const compassResult = engine.compassFilter.update(engine.vessel.heading);

  engine.lastLat = gpsResult.lat;
  engine.lastLng = gpsResult.lng;
  engine.lastHeading = compassResult;
}

/**
 * Get the current debug state for the debug panel.
 */
export function getDebugState(engine: SimEngineState, vesselCfg: VesselConfig): SimDebugState {
  const v = engine.vessel;
  const target = engine.pathIndex < engine.path.length
    ? engine.path[engine.pathIndex]
    : null;

  return {
    tick: engine.tickCount,
    behavior_state: engine.behavior.state,
    omega_cmd: engine.lastMotorCmd.omega_cmd,
    speed_cmd: engine.lastMotorCmd.speed_cmd,
    lat: v.lat,
    lng: v.lng,
    heading: v.heading,
    speed: v.speed,
    path_index: engine.pathIndex,
    total_path_points: engine.path.length,
    lookahead_lat: engine.lookaheadLat,
    lookahead_lng: engine.lookaheadLng,
    obstacle_obs: engine.perception.obstacle,
    trash_obs: engine.perception.trash,
    motor_cmd: engine.lastMotorCmd,
    nav_state: engine.paused ? 'paused' : engine.pathIndex >= engine.path.length ? 'completed' : 'navigating',
    gps_accepted: true,
  };
}
