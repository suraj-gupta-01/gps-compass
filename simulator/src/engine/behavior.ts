// Ported from backend/behaviour/behavior_manager.py — BehaviorManager
// Ported from backend/config.py — BehaviorConfig, TrashConfig, ObstacleConfig
// Exact state machine logic preserved: priority ordering, dwell guard, transitions.
// Uses the PRIORITY dict from fix #1 (explicit priority mapping, not string comparison).

import type { BehaviorStateName, SimObstacleObs, SimTrashObs, SimMotorCmd } from '../types';

// Port of BehaviorConfig
export const BEHAVIOR_CFG = {
  MIN_STATE_DWELL_S: 0.5,
  RETURN_TO_PATH_THRESHOLD_M: 3.0,
  REANCHOR_WINDOW: 30,
  TICK_HZ: 10.0,
};

// Port of TrashConfig
export const TRASH_CFG_BEHAVIOR = {
  TRASH_STEERING_KP: 0.10,
  TRASH_APPROACH_SPEED: 0.6,
  COLLECTION_AREA_FRAC: 0.15,
  CENTRE_DEADBAND_PX: 20.0,
  COLLECTION_TIMEOUT_S: 15.0,
  MAX_DEVIATIONS_PER_LANE: 3,
};

// Port of ObstacleConfig
export const OBSTACLE_CFG_BEHAVIOR = {
  OBSTACLE_REPULSION_GAIN: 25.0,
  OBSTACLE_WIDTH_FRAC_THRESHOLD: 0.10,
  OBSTACLE_SPEED_FACTOR: 0.4,
  OBSTACLE_CLEAR_FRAMES: 8,
  OBSTACLE_STOP_WIDTH_FRAC: 0.35,
};

// Explicit priority mapping — matches fix #1 applied to Python source.
const PRIORITY: Record<BehaviorStateName, number> = {
  avoid_obstacle: 4,
  collect_trash: 3,
  track_trash: 2,
  return_to_path: 1,
  navigate: 0,
};

// Port of BehaviorState enum values
export type BehaviorState = BehaviorStateName;

export const BehaviorStateValues = {
  NAVIGATE: 'navigate' as const,
  TRACK_TRASH: 'track_trash' as const,
  COLLECT_TRASH: 'collect_trash' as const,
  AVOID_OBSTACLE: 'avoid_obstacle' as const,
  RETURN_TO_PATH: 'return_to_path' as const,
};

// ── TrashTracker ────────────────────────────────────────────────────────────
// Port of perception/trash_tracker.py — TrashTracker

interface TrashTrackerState {
  active: boolean;
  startT: number | null;
  pathIndexAtDiversion: number | null;
  collected: boolean;
}

function newTrashTracker(): TrashTrackerState {
  return { active: false, startT: null, pathIndexAtDiversion: null, collected: false };
}

// ── BehaviorManager ─────────────────────────────────────────────────────────

export interface BehaviorManagerState {
  state: BehaviorState;
  prevState: BehaviorState;
  stateSince: number;
  obstacleClearCount: number;
  trashTracker: TrashTrackerState;
  returnTargetIndex: number | null;
  deviationsThisLane: number;
  lastLaneLabel: string;
  stateTransitions: number;
}

export function newBehaviorManager(): BehaviorManagerState {
  return {
    state: BehaviorStateValues.NAVIGATE,
    prevState: BehaviorStateValues.NAVIGATE,
    stateSince: 0,
    obstacleClearCount: 0,
    trashTracker: newTrashTracker(),
    returnTargetIndex: null,
    deviationsThisLane: 0,
    lastLaneLabel: '',
    stateTransitions: 0,
  };
}

function transition(
  bm: BehaviorManagerState,
  newState: BehaviorState,
  pathIndex: number,
  now: number,
): void {
  // Port of BehaviorManager._transition()
  const old = bm.state;
  bm.prevState = old;
  bm.state = newState;
  bm.stateSince = now;
  bm.stateTransitions++;

  if (newState === BehaviorStateValues.TRACK_TRASH) {
    bm.trashTracker.active = true;
    bm.trashTracker.startT = now;
    bm.trashTracker.collected = false;
    bm.trashTracker.pathIndexAtDiversion = pathIndex;
    bm.deviationsThisLane++;
  } else if (newState === BehaviorStateValues.AVOID_OBSTACLE) {
    bm.trashTracker.active = false;
    bm.obstacleClearCount = 0;
  } else if (newState === BehaviorStateValues.RETURN_TO_PATH) {
    bm.returnTargetIndex = bm.trashTracker.pathIndexAtDiversion ?? pathIndex;
  } else if (newState === BehaviorStateValues.NAVIGATE) {
    bm.obstacleClearCount = 0;
  }
}

function selectState(
  bm: BehaviorManagerState,
  obs: SimObstacleObs | null,
  trash: SimTrashObs | null,
  collectionTriggered: boolean,
): BehaviorState {
  // Port of BehaviorManager._select_state()

  // Priority 1: Obstacle — pre-empts everything
  if (obs !== null && obs.width_frac >= OBSTACLE_CFG_BEHAVIOR.OBSTACLE_WIDTH_FRAC_THRESHOLD) {
    bm.obstacleClearCount = 0;
    return BehaviorStateValues.AVOID_OBSTACLE;
  }

  // If we WERE avoiding obstacle, require N clear frames before exiting
  if (bm.state === BehaviorStateValues.AVOID_OBSTACLE) {
    if (obs === null || obs.width_frac < OBSTACLE_CFG_BEHAVIOR.OBSTACLE_WIDTH_FRAC_THRESHOLD) {
      bm.obstacleClearCount++;
    } else {
      bm.obstacleClearCount = 0;
    }
    if (bm.obstacleClearCount < OBSTACLE_CFG_BEHAVIOR.OBSTACLE_CLEAR_FRAMES) {
      return BehaviorStateValues.AVOID_OBSTACLE;
    }
    return BehaviorStateValues.RETURN_TO_PATH;
  }

  // Priority 2: Collection trigger (already tracking, area threshold hit)
  if (
    (bm.state === BehaviorStateValues.TRACK_TRASH || bm.state === BehaviorStateValues.COLLECT_TRASH) &&
    trash !== null &&
    collectionTriggered
  ) {
    return BehaviorStateValues.COLLECT_TRASH;
  }

  // Priority 3: Trash tracking
  if (
    trash !== null &&
    bm.state !== BehaviorStateValues.COLLECT_TRASH &&
    bm.state !== BehaviorStateValues.RETURN_TO_PATH &&
    bm.deviationsThisLane < TRASH_CFG_BEHAVIOR.MAX_DEVIATIONS_PER_LANE
  ) {
    return BehaviorStateValues.TRACK_TRASH;
  }

  // Trash gone while tracking
  if (bm.state === BehaviorStateValues.TRACK_TRASH && trash === null) {
    if (bm.trashTracker.active) {
      return BehaviorStateValues.TRACK_TRASH;
    }
    return BehaviorStateValues.RETURN_TO_PATH;
  }

  // RETURN_TO_PATH stays until re-anchor complete (handled in execute)
  if (bm.state === BehaviorStateValues.RETURN_TO_PATH) {
    return BehaviorStateValues.RETURN_TO_PATH;
  }

  return BehaviorStateValues.NAVIGATE;
}

// Port of TrashTracker.update() — visual servoing
function trashTrackerUpdate(trash: SimTrashObs | null, now: number, tracker: TrashTrackerState): SimMotorCmd {
  if (tracker.startT !== null && now - tracker.startT > TRASH_CFG_BEHAVIOR.COLLECTION_TIMEOUT_S) {
    tracker.active = false;
    return { omega_cmd: 0, speed_cmd: 0, left_on: false, right_on: false, blinking: false, steering_label: 'stop' };
  }

  if (trash === null) {
    return {
      omega_cmd: 0,
      speed_cmd: TRASH_CFG_BEHAVIOR.TRASH_APPROACH_SPEED * 0.3,
      left_on: true,
      right_on: true,
      blinking: false,
      steering_label: 'forward',
    };
  }

  let errorX = trash.image_cx_norm;
  const deadbandNorm = TRASH_CFG_BEHAVIOR.CENTRE_DEADBAND_PX / 320.0;
  if (Math.abs(errorX) < deadbandNorm) errorX = 0;

  const omegaCmd = TRASH_CFG_BEHAVIOR.TRASH_STEERING_KP * errorX * 90.0;
  const speedCmd = TRASH_CFG_BEHAVIOR.TRASH_APPROACH_SPEED * Math.max(0.3, 1.0 - Math.abs(errorX));

  let leftOn: boolean, rightOn: boolean;
  if (Math.abs(omegaCmd) < 1.5) { leftOn = true; rightOn = true; }
  else if (omegaCmd > 0) { leftOn = true; rightOn = false; }
  else { leftOn = false; rightOn = true; }

  return {
    omega_cmd: omegaCmd,
    speed_cmd: speedCmd,
    left_on: leftOn,
    right_on: rightOn,
    blinking: false,
    steering_label: leftOn && rightOn ? 'forward' : leftOn ? 'turn_right' : 'turn_left',
  };
}

// Port of obstacle avoidance
function obstacleAvoidanceCmd(obs: SimObstacleObs | null, ppSpeed: number): SimMotorCmd {
  if (obs === null) {
    return { omega_cmd: 0, speed_cmd: ppSpeed * 0.5, left_on: true, right_on: true, blinking: false, steering_label: 'forward' };
  }

  if (obs.width_frac >= OBSTACLE_CFG_BEHAVIOR.OBSTACLE_STOP_WIDTH_FRAC) {
    return { omega_cmd: 0, speed_cmd: 0, left_on: false, right_on: false, blinking: true, steering_label: 'stop' };
  }

  const omegaCmd = -OBSTACLE_CFG_BEHAVIOR.OBSTACLE_REPULSION_GAIN * obs.image_cx_norm;
  const proximity = Math.min(obs.width_frac / OBSTACLE_CFG_BEHAVIOR.OBSTACLE_STOP_WIDTH_FRAC, 1.0);
  const speedCmd = ppSpeed * OBSTACLE_CFG_BEHAVIOR.OBSTACLE_SPEED_FACTOR * (1.0 - proximity * 0.5);

  let leftOn: boolean, rightOn: boolean;
  if (omegaCmd > 2.0) { leftOn = true; rightOn = false; }
  else if (omegaCmd < -2.0) { leftOn = false; rightOn = true; }
  else { leftOn = true; rightOn = true; }

  return {
    omega_cmd: omegaCmd,
    speed_cmd: speedCmd,
    left_on: leftOn,
    right_on: rightOn,
    blinking: false,
    steering_label: leftOn && rightOn ? 'forward' : leftOn ? 'turn_right' : 'turn_left',
  };
}

function ppToCmd(omega: number, speed: number): SimMotorCmd {
  // Port of BehaviorManager._pp_to_cmd()
  let leftOn: boolean, rightOn: boolean;
  if (Math.abs(omega) < 1.5) { leftOn = true; rightOn = true; }
  else if (omega > 0) { leftOn = true; rightOn = false; }
  else { leftOn = false; rightOn = true; }

  const label = !leftOn && !rightOn ? 'stop' : leftOn && rightOn ? 'forward' : leftOn ? 'turn_right' : 'turn_left';
  return { omega_cmd: omega, speed_cmd: speed, left_on: leftOn, right_on: rightOn, blinking: false, steering_label: label };
}

/**
 * Main behavior arbitration entry point.
 * Port of BehaviorManager.arbitrate() + _execute()
 */
export function arbitrate(
  bm: BehaviorManagerState,
  obs: SimObstacleObs | null,
  trash: SimTrashObs | null,
  ppOmega: number,
  ppSpeed: number,
  currentPathIndex: number,
  currentSegmentLabel: string,
  now: number,
  collectionTriggered: boolean,
): SimMotorCmd {
  // 1. Update per-lane deviation counter
  if (currentSegmentLabel !== bm.lastLaneLabel) {
    bm.lastLaneLabel = currentSegmentLabel;
    bm.deviationsThisLane = 0;
  }

  // 2. Determine next state
  const nextState = selectState(bm, obs, trash, collectionTriggered);

  // 3. Apply dwell guard (prevent micro-oscillations)
  const dwell = now - bm.stateSince;
  if (
    nextState !== bm.state &&
    dwell < BEHAVIOR_CFG.MIN_STATE_DWELL_S &&
    PRIORITY[nextState] < PRIORITY[bm.state]
  ) {
    // Lower-priority state wants to fire; respect dwell time
  } else {
    // 4. Execute state transition if needed
    if (nextState !== bm.state) {
      transition(bm, nextState, currentPathIndex, now);
    }
  }

  // 5. Execute current state → produce motor command
  const state = bm.state;

  if (state === BehaviorStateValues.NAVIGATE) {
    return ppToCmd(ppOmega, ppSpeed);
  }

  if (state === BehaviorStateValues.TRACK_TRASH) {
    return trashTrackerUpdate(trash, now, bm.trashTracker);
  }

  if (state === BehaviorStateValues.COLLECT_TRASH) {
    if (!bm.trashTracker.collected) {
      bm.trashTracker.collected = true;
      bm.trashTracker.active = false;
    }
    transition(bm, BehaviorStateValues.RETURN_TO_PATH, currentPathIndex, now);
    return { omega_cmd: 0, speed_cmd: 0, left_on: false, right_on: false, blinking: false, steering_label: 'stop' };
  }

  if (state === BehaviorStateValues.AVOID_OBSTACLE) {
    return obstacleAvoidanceCmd(obs, ppSpeed);
  }

  if (state === BehaviorStateValues.RETURN_TO_PATH) {
    transition(bm, BehaviorStateValues.NAVIGATE, currentPathIndex, now);
    return ppToCmd(ppOmega, ppSpeed);
  }

  return ppToCmd(ppOmega, ppSpeed);
}
