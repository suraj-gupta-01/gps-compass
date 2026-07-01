// Ported from backend/navigation/controller.py — PurePursuitController
// Exact math preserved: segment-circle intersection lookahead, proportional heading + speed.

import type { VesselConfig, PathPoint } from '../types';
import { haversine, bearing, headingError, METERS_PER_DEG_LAT, metersPerDegLng } from './geo';

/**
 * Port of PurePursuitController.find_lookahead()
 * Parametric segment-circle intersection (Coulter 1992).
 */
export function findLookahead(
  vesselLat: number,
  vesselLng: number,
  pathPoints: PathPoint[],
  currentIndex: number,
  lookaheadM: number,
): { lat: number; lng: number; index: number } {
  const ld = lookaheadM;
  const ld2 = ld * ld;
  const n = pathPoints.length;

  if (currentIndex >= n) {
    const last = pathPoints[n - 1];
    return { lat: last.lat, lng: last.lng, index: n - 1 };
  }

  const mPerLng = metersPerDegLng(vesselLat);

  for (let i = currentIndex; i < Math.min(n - 1, currentIndex + 50); i++) {
    const a = pathPoints[i];
    const b = pathPoints[i + 1];

    const ax = (a.lng - vesselLng) * mPerLng;
    const ay = (a.lat - vesselLat) * METERS_PER_DEG_LAT;
    const bx = (b.lng - vesselLng) * mPerLng;
    const by = (b.lat - vesselLat) * METERS_PER_DEG_LAT;

    const dx = bx - ax;
    const dy = by - ay;

    const dr2 = dx * dx + dy * dy;
    if (dr2 < 1e-10) continue;

    const fDotD = ax * dx + ay * dy;
    const fSq = ax * ax + ay * ay;

    const aCoef = dr2;
    const bCoef = 2.0 * fDotD;
    const cCoef = fSq - ld2;

    const discriminant = bCoef * bCoef - 4.0 * aCoef * cCoef;
    if (discriminant < 0) continue;

    const sqrtDisc = Math.sqrt(discriminant);
    const t2 = (-bCoef + sqrtDisc) / (2.0 * aCoef);
    const t1 = (-bCoef - sqrtDisc) / (2.0 * aCoef);

    let t: number | null = null;
    if (0.0 <= t2 && t2 <= 1.0) t = t2;
    else if (0.0 <= t1 && t1 <= 1.0) t = t1;

    if (t === null) continue;

    const ixM = ax + t * dx;
    const iyM = ay + t * dy;
    const laLat = vesselLat + iyM / METERS_PER_DEG_LAT;
    const laLng = vesselLng + ixM / mPerLng;

    return { lat: laLat, lng: laLng, index: i + 1 };
  }

  // Fallback
  const target = pathPoints[Math.min(currentIndex, n - 1)];
  return { lat: target.lat, lng: target.lng, index: currentIndex };
}

/**
 * Port of PurePursuitController.compute_omega()
 * Proportional heading controller: omega_cmd = Kp * heading_error
 */
export function computeOmega(
  vesselLat: number,
  vesselLng: number,
  vesselHeading: number,
  lookaheadLat: number,
  lookaheadLng: number,
  headingKp: number,
): number {
  const req = bearing(vesselLat, vesselLng, lookaheadLat, lookaheadLng);
  const err = headingError(vesselHeading, req);
  return headingKp * err;
}

/**
 * Port of PurePursuitController.compute_speed()
 * Reduce speed proportionally when heading error is large.
 */
export function computeSpeed(
  headingErrorDeg: number,
  cruiseSpeed: number,
  turnSpeedFactor: number,
): number {
  const factor = Math.max(turnSpeedFactor, 1.0 - Math.abs(headingErrorDeg) / 90.0);
  return cruiseSpeed * factor;
}

/**
 * Port of PurePursuitController.steering_from_omega()
 */
export function steeringFromOmega(omega: number, maxRate: number): string {
  const ratio = maxRate > 0 ? omega / maxRate : 0;
  if (Math.abs(ratio) < 0.15) return 'forward';
  if (Math.abs(ratio) < 0.90) return omega > 0 ? 'turn_right' : 'turn_left';
  return 'large_correction';
}
