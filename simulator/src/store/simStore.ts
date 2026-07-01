// What it does: owns AeroSim UI state, current SimEngine, standalone ticks,
// HIL backend feeds, and object/mission editing actions.
// Imports from: Zustand, simulator engine modules, backend bridge services,
// and shared simulator/backend types.
// Behavior: preserves standalone mode; HIL mode is additive and all network
// feed calls inside doTick are fire-and-forget.

import { create } from 'zustand';
import type {
  VesselConfig, CameraConfig, LatLng, SimObstacle, SimTrash,
  SimDebugState, ToolbarMode, PathPoint, BackendTelemetryFrame,
  BackendMotorCmd, SimStatusResponse, HilFeedStats,
} from '../types';
import { defaultVesselConfig } from '../engine/kinematics';
import {
  newSimEngine, loadPath, startSim, pauseSim, resumeSim, resetSim, tick, getDebugState,
  collectSyntheticPerception,
  type SimEngineState,
} from '../engine/SimEngine';
import {
  buildMissionPayload,
  connectHILTelemetry,
  disconnectBackendTelemetry,
  getSimStatus,
  sendSimPerception,
  sendSimTelemetry,
  sendBackendCommand,
  uploadMissionToBackend,
} from '../services/backendBridge';

const defaultCamera: CameraConfig = {
  hfov_deg: 90,
  max_range_m: 30,
  frame_w: 640,
  frame_h: 640,
};

let engine: SimEngineState = newSimEngine(defaultVesselConfig(), defaultCamera);

export type SimMode = 'standalone' | 'hil';

export interface HilState {
  status: SimStatusResponse | null;
  ready: boolean;
  backendMotorCmd: BackendMotorCmd | null;
  backendVessel: { lat: number; lng: number; heading: number } | null;
  compassNoise: boolean;
  gpsNoise: boolean;
  feedStats: HilFeedStats;
  lastMissionResponse: string | null;
}

function gaussianNoise(): number {
  const u = Math.max(Math.random(), Number.EPSILON);
  const v = Math.max(Math.random(), Number.EPSILON);
  return Math.sqrt(-2.0 * Math.log(u)) * Math.cos(2.0 * Math.PI * v);
}

function noisyTelemetry(hil: HilState): { lat: number; lng: number; heading: number } {
  let { lat, lng, heading } = engine.vessel;
  if (hil.compassNoise) {
    const t = performance.now() / 1000;
    heading = (heading + Math.sin(t * 4.1) * 2.0 + Math.sin(t * 11.3) * 0.8 + 360) % 360;
  }
  if (hil.gpsNoise) {
    const northM = gaussianNoise() * 0.5;
    const eastM = gaussianNoise() * 0.5;
    lat += northM / 110540.0;
    lng += eastM / (111320.0 * Math.cos((lat * Math.PI) / 180));
  }
  return { lat, lng, heading };
}

function rate(now: number, bucket: number[]): number {
  while (bucket.length && now - bucket[0] > 1000) bucket.shift();
  return bucket.length;
}

const hilTelemetrySends: number[] = [];
const hilPerceptionSends: number[] = [];
const hilWsReceives: number[] = [];

export interface SimStore {
  // Engine reference
  engine: SimEngineState;
  // Vessel config (live-editable)
  vesselCfg: VesselConfig;
  camera: CameraConfig;
  // Map state
  geofence: LatLng[];
  gcsPoint: LatLng | null;
  generatedPath: PathPoint[];
  sweepWidth: number;
  // Objects
  obstacles: SimObstacle[];
  trash: SimTrash[];
  // UI state
  toolbarMode: ToolbarMode;
  debugState: SimDebugState | null;
  simRunning: boolean;
  simPaused: boolean;
  backendUrl: string;
  useBackend: boolean;
  backendStatus: 'disconnected' | 'connecting' | 'connected' | 'error';
  backendTelemetry: BackendTelemetryFrame | null;
  simMode: SimMode;
  hilState: HilState;
  simSpeed: number;
  // Actions
  setVesselCfg: (cfg: Partial<VesselConfig>) => void;
  setCamera: (cfg: Partial<CameraConfig>) => void;
  setSweepWidth: (w: number) => void;
  setToolbarMode: (mode: ToolbarMode) => void;
  setBackendUrl: (url: string) => void;
  setUseBackend: (enabled: boolean) => void;
  setSimMode: (mode: SimMode) => void;
  testHilConnection: () => void;
  setHilNoise: (kind: 'compass' | 'gps', enabled: boolean) => void;
  setSimSpeed: (v: number) => void;
  connectBackend: () => void;
  disconnectBackend: () => void;
  syncMissionToBackend: () => void;
  sendCommandToBackend: (command: string) => void;
  addGeofencePoint: (p: LatLng) => void;
  clearGeofence: () => void;
  setGcsPoint: (p: LatLng) => void;
  setGeneratedPath: (path: PathPoint[]) => void;
  addObstacle: (obs: SimObstacle) => void;
  addTrash: (t: SimTrash) => void;
  updateObstacle: (id: string, lat: number, lng: number, radius_m: number) => void;
  updateTrash: (id: string, lat: number, lng: number, area_cm2?: number) => void;
  resizeObstacle: (id: string, radius_m: number) => void;
  resizeTrash: (id: string, area_cm2: number) => void;
  removeObstacle: (id: string) => void;
  removeTrash: (id: string) => void;
  clearObjects: () => void;
  loadMission: (path: PathPoint[]) => void;
  start: () => void;
  pause: () => void;
  resume: () => void;
  reset: () => void;
  doTick: () => void;
  refreshDebug: () => void;
}

export const useSimStore = create<SimStore>((set, get) => ({
  engine,
  vesselCfg: defaultVesselConfig(),
  camera: defaultCamera,
  geofence: [],
  gcsPoint: null,
  generatedPath: [],
  sweepWidth: 5,
  obstacles: [],
  trash: [],
  toolbarMode: 'none',
  debugState: null,
  simRunning: false,
  simPaused: true,
  backendUrl: 'http://localhost:8000',
  useBackend: false,
  backendStatus: 'disconnected',
  backendTelemetry: null,
  simSpeed: 1.0,

  setVesselCfg: (cfg) => set((s) => ({ vesselCfg: { ...s.vesselCfg, ...cfg } })),
  setCamera: (cfg) => set((s) => ({ camera: { ...s.camera, ...cfg } })),
  setSweepWidth: (w) => set({ sweepWidth: w }),
  setToolbarMode: (mode) => set({ toolbarMode: mode }),
  setBackendUrl: (url) => {
    set({ backendUrl: url });
    if (get().useBackend) {
      get().connectBackend();
    }
  },
  setUseBackend: (enabled) => {
    set({ useBackend: enabled });
    if (enabled) {
      get().connectBackend();
    } else {
      disconnectBackendTelemetry();
      set({ backendStatus: 'disconnected', backendTelemetry: null });
    }
  },
  setSimSpeed: (v: number) => set({ simSpeed: v }),
  connectBackend: () => {
    const { backendUrl, useBackend } = get();
    if (!useBackend) return;
    set({ backendStatus: 'connecting' });
    disconnectBackendTelemetry();
    connectBackendTelemetry(
      backendUrl,
      (frame) => set({ backendTelemetry: frame }),
      (connected, message) => {
        set({ backendStatus: connected ? 'connected' : 'error' });
        if (!connected && message !== 'connecting') {
          set({ backendTelemetry: null });
        }
      },
    );
  },
  disconnectBackend: () => {
    disconnectBackendTelemetry();
    set({ backendStatus: 'disconnected', backendTelemetry: null });
  },
  syncMissionToBackend: () => {
    const { useBackend, backendUrl, geofence, gcsPoint, sweepWidth, generatedPath } = get();
    if (!useBackend) return;

    const payload = buildMissionPayload({ geofence, gcsPoint, sweepWidth, generatedPath });
    void uploadMissionToBackend(backendUrl, payload).catch((error) => {
      console.warn('Backend mission sync failed', error);
      set({ backendStatus: 'error' });
    });
  },
  sendCommandToBackend: (command) => {
    const { useBackend, backendUrl } = get();
    if (!useBackend) return;

    void sendBackendCommand(backendUrl, command).catch((error) => {
      console.warn('Backend command failed', error);
      set({ backendStatus: 'error' });
    });
  },

  addGeofencePoint: (p) => set((s) => ({ geofence: [...s.geofence, p] })),
  clearGeofence: () => set({ geofence: [], generatedPath: [] }),
  setGcsPoint: (p) => set({ gcsPoint: p }),
  setGeneratedPath: (path) => set({ generatedPath: path }),

  addObstacle: (obs) => {
    engine.obstacles.push(obs);
    set({ obstacles: [...engine.obstacles] });
  },
  addTrash: (t) => {
    engine.trash.push(t);
    set({ trash: [...engine.trash] });
  },
  updateObstacle: (id, lat, lng, radius_m) => {
    const obs = engine.obstacles.find(o => o.id === id);
    if (obs) { obs.lat = lat; obs.lng = lng; obs.radius_m = radius_m; }
    set({ obstacles: [...engine.obstacles] });
  },
  updateTrash: (id, lat, lng) => {
    const t = engine.trash.find(x => x.id === id);
    if (t) { t.lat = lat; t.lng = lng; }
    set({ trash: [...engine.trash] });
  },
  removeObstacle: (id) => {
    engine.obstacles = engine.obstacles.filter(o => o.id !== id);
    set({ obstacles: [...engine.obstacles] });
  },
  removeTrash: (id) => {
    engine.trash = engine.trash.filter(t => t.id !== id);
    set({ trash: [...engine.trash] });
  },
  clearObjects: () => {
    engine.obstacles = [];
    engine.trash = [];
    set({ obstacles: [], trash: [] });
  },

  loadMission: (path) => {
    loadPath(engine, path);
    set({
      generatedPath: path,
      simRunning: engine.running,
      simPaused: engine.paused,
    });
    get().syncMissionToBackend();
  },

  start: () => {
    startSim(engine);
    set({ simRunning: engine.running, simPaused: engine.paused });
    get().sendCommandToBackend('start');
  },
  pause: () => {
    pauseSim(engine);
    set({ simPaused: engine.paused });
    get().sendCommandToBackend('pause');
  },
  resume: () => {
    resumeSim(engine);
    set({ simPaused: engine.paused });
    get().sendCommandToBackend('resume');
  },
  reset: () => {
    resetSim(engine);
    set({ simRunning: engine.running, simPaused: engine.paused });
    get().sendCommandToBackend('reset');
  },

  doTick: () => {
    const s = get();
    // Scale dt by simSpeed for faster/slower demos
    const dt = 0.1 * (s.simSpeed || 1.0);
    tick(engine, s.vesselCfg, dt);
  },

  refreshDebug: () => {
    const s = get();
    const ds = getDebugState(engine, s.vesselCfg);
    set({ debugState: ds, simPaused: engine.paused });
  },
}));
