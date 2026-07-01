// Ported from backend/perception/perception_manager.py — PerceptionManager, _TrackSlot
// and backend/perception/detection.py — BBox, RawDetection, ObstacleObservation, TrashObservation
// Ported from backend/config.py — PerceptionConfig
// Exact temporal confirm/decay logic preserved.
// What it does: ports backend/perception/perception_manager.py and FOV geometry
// for standalone AeroSim perception filtering and HIL synthetic detections.
// Imports from: simulator types and engine/geo helpers.
// Behavior: preserves standalone behavior while fixing tick ordering to match
// Python PerceptionManager.update(); HIL uses the same FOV projection for feeds.

import type { SimObstacle, SimTrash, SimObstacleObs, SimTrashObs, CameraConfig } from '../types';
import { METERS_PER_DEG_LAT, metersPerDegLng } from './geo';

// Port of PerceptionConfig
export const PERCEPTION_CFG = {
  CLASS_OBSTACLE: 0,
  CLASS_TRASH: 1,
  TRASH_CONFIDENCE_THRESHOLD: 0.45,
  OBSTACLE_CONFIDENCE_THRESHOLD: 0.40,
  MIN_CONFIRM_FRAMES: 2,
  MAX_MISS_FRAMES: 5,
  MIN_BBOX_AREA_PX2: 400.0,
  MAX_TRACKED_OBSTACLES: 8,
  MAX_TRACKED_TRASH: 4,
};

// Port of TrashConfig (collection trigger)
export const TRASH_CFG = {
  COLLECTION_AREA_FRAC: 0.15,
  TRASH_STEERING_KP: 0.10,
  TRASH_APPROACH_SPEED: 0.06,
  CENTRE_DEADBAND_PX: 20.0,
  COLLECTION_TIMEOUT_S: 15.0,
  MAX_DEVIATIONS_PER_LANE: 3,
};

// Port of ObstacleConfig
export const OBSTACLE_CFG = {
  OBSTACLE_REPULSION_GAIN: 25.0,
  OBSTACLE_WIDTH_FRAC_THRESHOLD: 0.10,
  OBSTACLE_SPEED_FACTOR: 0.4,
  OBSTACLE_CLEAR_FRAMES: 8,
  OBSTACLE_STOP_WIDTH_FRAC: 0.35,
};

/**
 * Port of _TrackSlot — single-class detection tracker with confirm/decay.
 */
class TrackSlot {
  classId: number;
  confirmCount = 0;
  missCount = 0;
  confirmed = false;
  bestDet: { confidence: number; area: number; cx: number; cy: number; w: number; h: number; fw: number; fh: number } | null = null;

  constructor(classId: number) {
    this.classId = classId;
  }

  feed(det: { confidence: number; area: number; cx: number; cy: number; w: number; h: number; fw: number; fh: number }, minConfirm: number): void {
    // Port of _TrackSlot.feed()
    this.missCount = 0;
    if (this.bestDet === null || det.confidence * det.area > this.bestDet.confidence * this.bestDet.area) {
      this.bestDet = det;
    }
    this.confirmCount++;
    if (this.confirmCount >= minConfirm) {
      this.confirmed = true;
    }
  }

  decay(maxMiss: number): void {
    // Port of _TrackSlot.decay()
    this.missCount++;
    this.confirmCount = Math.max(0, this.confirmCount - 1);
    if (this.missCount > maxMiss) {
      this.confirmed = false;
      this.confirmCount = 0;
    }
    this.bestDet = null;
  }

  resetTick(): void {
    this.bestDet = null;
  }
}

/**
 * Port of PerceptionManager — drains detection queue, maintains confirmed state.
 * In the simulator, "draining the queue" is replaced by feeding FOV-detected objects.
 */
export class PerceptionManager {
  private obstacleSlot: TrackSlot;
  private trashSlot: TrackSlot;
  private obstacleObs: SimObstacleObs | null = null;
  private trashObs: SimTrashObs | null = null;

  constructor() {
    this.obstacleSlot = new TrackSlot(PERCEPTION_CFG.CLASS_OBSTACLE);
    this.trashSlot = new TrackSlot(PERCEPTION_CFG.CLASS_TRASH);
  }

  get obstacle(): SimObstacleObs | null { return this.obstacleObs; }
  get trash(): SimTrashObs | null { return this.trashObs; }
  get obstacleActive(): boolean { return this.obstacleObs !== null; }
  get trashActive(): boolean { return this.trashObs !== null; }

  /**
   * Feed a detected obstacle into the temporal filter.
   * det should be in pixel-space (640x640 frame).
   */
  feedObstacleDet(cxNorm: number, widthFrac: number, confidence: number): void {
    const fw = 640;
    const fh = 640;
    const cx = (cxNorm + 1) * (fw / 2); // denormalize to pixel x
    const w = widthFrac * fw;
    const h = w; // approximate square
    this.obstacleSlot.feed({ confidence, area: w * h, cx, cy: fh / 2, w, h, fw, fh }, PERCEPTION_CFG.MIN_CONFIRM_FRAMES);
  }

  /**
   * Feed a detected trash into the temporal filter.
   */
  feedTrashDet(cxNorm: number, cyNorm: number, areaFrac: number, confidence: number): void {
    const fw = 640;
    const fh = 640;
    const cx = (cxNorm + 1) * (fw / 2);
    const cy = (cyNorm + 1) * (fh / 2);
    const w = Math.sqrt(areaFrac * fw * fh);
    const h = w;
    this.trashSlot.feed({ confidence, area: w * h, cx, cy, w, h, fw, fh }, PERCEPTION_CFG.MIN_CONFIRM_FRAMES);
  }

  /**
   * Clear per-tick transients before FOV detections are fed.
   * Port of backend/perception/perception_manager.py PerceptionManager.update()
   * step 1; TS feeds detections immediately after this because it has no queue.
   */
  resetTick(): void {
    this.obstacleSlot.resetTick();
    this.trashSlot.resetTick();
  }

  /**
   * Decay un-fed slots and publish observations after FOV detections are fed.
   * Port of backend/perception/perception_manager.py PerceptionManager.update()
   * steps 3-4. This must run after resetTick() and feed*Det().
   */
  finalizeTick(): void {
    if (this.obstacleSlot.bestDet === null) {
      this.obstacleSlot.decay(PERCEPTION_CFG.MAX_MISS_FRAMES);
    }
    if (this.trashSlot.bestDet === null) {
      this.trashSlot.decay(PERCEPTION_CFG.MAX_MISS_FRAMES);
    }

    // 3. Build confirmed observations (or clear them)
    this.obstacleObs = this.buildObstacleObs();
    this.trashObs = this.buildTrashObs();
  }

  /**
   * Compatibility wrapper for older callers. New tick code should call
   * resetTick(), feed detections, then finalizeTick().
   */
  update(): void {
    this.finalizeTick();
  }

  private buildObstacleObs(): SimObstacleObs | null {
    // Port of PerceptionManager._build_obstacle_obs()
    const slot = this.obstacleSlot;
    if (!slot.confirmed || slot.bestDet === null) return null;
    const det = slot.bestDet;
    const fw = det.fw;
    const cxNorm = (det.cx - fw / 2.0) / (fw / 2.0);
    const wFrac = det.w / fw;
    return { image_cx_norm: cxNorm, width_frac: wFrac, confidence: det.confidence };
  }

  private buildTrashObs(): SimTrashObs | null {
    // Port of PerceptionManager._build_trash_obs()
    const slot = this.trashSlot;
    if (!slot.confirmed || slot.bestDet === null) return null;
    const det = slot.bestDet;
    const fw = det.fw;
    const fh = det.fh;
    const cxNorm = (det.cx - fw / 2.0) / (fw / 2.0);
    const cyNorm = (det.cy - fh / 2.0) / (fh / 2.0);
    const areaFrac = det.area / (fw * fh);
    return { image_cx_norm: cxNorm, image_cy_norm: cyNorm, area_frac: areaFrac, confidence: det.confidence };
  }

  reset(): void {
    this.obstacleSlot = new TrackSlot(PERCEPTION_CFG.CLASS_OBSTACLE);
    this.trashSlot = new TrackSlot(PERCEPTION_CFG.CLASS_TRASH);
    this.obstacleObs = null;
    this.trashObs = null;
  }
}

// ── FOV-based detection (simulator-specific) ───────────────────────────────

/**
 * Check if a world-space point is within the vessel's camera FOV.
 * Returns bearing offset from vessel heading and distance, or null if out of FOV/range.
 */
export function isInFOV(
  vesselLat: number, vesselLng: number, vesselHeading: number,
  objLat: number, objLng: number,
  hfovDeg: number, maxRangeM: number,
): { bearingOffset: number; distance: number } | null {
  // Port of backend/perception logic: bearing + distance check
  const dx = (objLng - vesselLng) * metersPerDegLng(vesselLat);
  const dy = (objLat - vesselLat) * METERS_PER_DEG_LAT;
  const dist = Math.sqrt(dx * dx + dy * dy);

  if (dist > maxRangeM || dist < 0.1) return null;

  const objBearing = ((Math.atan2(dx, dy) * 180) / Math.PI + 360) % 360;
  let offset = objBearing - vesselHeading;
  if (offset > 180) offset -= 360;
  if (offset < -180) offset += 360;

  if (Math.abs(offset) > hfovDeg / 2) return null;

  return { bearingOffset: offset, distance: dist };
}

/**
 * Convert a world-space object position to a synthetic bounding box
 * at the configured frame resolution, matching the Python detection pipeline.
 */
export function worldToFrame(
  bearingOffset: number,
  distance: number,
  objectRadiusM: number,
  frameW: number,
  frameH: number,
  hfovDeg: number,
): { cx: number; cy: number; w: number; h: number } {
  // Apparent width in metres at this distance
  const apparentW = (objectRadiusM * 2 * frameW) / (2 * distance * Math.tan((hfovDeg * Math.PI) / 360));
  const apparentH = apparentW; // assume roughly square for obstacles

  // cx in pixels: bearing offset maps to horizontal position in frame
  const cxPx = (bearingOffset / (hfovDeg / 2)) * (frameW / 2) + frameW / 2;
  const cyPx = frameH / 2; // objects appear at vertical centre for simplicity

  return { cx: cxPx, cy: cyPx, w: Math.max(apparentW, 1), h: Math.max(apparentH, 1) };
}
