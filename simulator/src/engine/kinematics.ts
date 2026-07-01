// Ported from backend/navigation/kinematics.py — VesselKinematics
// Exact math preserved: rate-limited omega, first-order speed lag, forward Euler position integration.

import type { VesselConfig } from '../types';
import { METERS_PER_DEG_LAT, metersPerDegLng } from './geo';

export function defaultVesselConfig(): VesselConfig {
  return {
    cruise_speed_mps: 1.5,
    max_speed_mps: 2.5,
    speed_tau: 2.0,
    max_turn_rate_dps: 15.0,
    turn_speed_factor: 0.6,
    lookahead_m: 8.0,
    waypoint_radius_m: 4.0,
    heading_kp: 0.4,
    length_m: 1.0,
    width_m: 0.6,
  };
}

export interface VesselState {
  lat: number;
  lng: number;
  heading: number; // degrees, 0=North, clockwise
  speed: number;   // m/s
}

export function resetVesselState(lat: number, lng: number, heading = 0): VesselState {
  return { lat, lng, heading, speed: 0 };
}

/**
 * Port of VesselKinematics.step()
 * Advances vessel state by dt seconds given commanded omega and speed.
 */
export function stepKinematics(
  state: VesselState,
  cfg: VesselConfig,
  omega_cmd: number, // deg/s, +ve = turn right
  dt: number,
  speed_cmd?: number,
): VesselState {
  if (speed_cmd === undefined) speed_cmd = cfg.cruise_speed_mps;

  // 1. Rate-limit angular velocity
  const omega = Math.max(-cfg.max_turn_rate_dps, Math.min(cfg.max_turn_rate_dps, omega_cmd));

  // 2. Integrate heading
  let heading = (state.heading + omega * dt) % 360;
  if (heading < 0) heading += 360;

  // 3. Reduce speed during hard turns
  const turnFraction = Math.min(Math.abs(omega) / cfg.max_turn_rate_dps, 1.0);
  const effectiveSpeedCmd = speed_cmd * (1.0 - turnFraction * (1.0 - cfg.turn_speed_factor));

  // 4. First-order speed lag (Euler step)
  const alpha = dt / cfg.speed_tau;
  let speed = state.speed + alpha * (effectiveSpeedCmd - state.speed);
  speed = Math.max(0.0, Math.min(speed, cfg.max_speed_mps));

  // 5. Integrate position (forward Euler in local metric plane)
  const dist = speed * dt;
  const hdgRad = (heading * Math.PI) / 180;
  const dx_m = dist * Math.sin(hdgRad); // East
  const dy_m = dist * Math.cos(hdgRad); // North

  const mPerLng = metersPerDegLng(state.lat);
  const lat = state.lat + (dy_m / METERS_PER_DEG_LAT);
  const lng = state.lng + (dx_m / mPerLng);

  return { lat, lng, heading, speed };
}
