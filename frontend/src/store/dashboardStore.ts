import { create } from 'zustand';
import type {
  DashboardStore, ConnectionStatus, MapLayer,
  PlannerMissionJSON, LatLng, TelemetryFrame,
} from '../types';

const TRAIL_MAX = 200;

export const useDashboardStore = create<DashboardStore>((set) => ({
  wsStatus: 'disconnected',
  backendUrl: 'ws://localhost:8000',
  missionLoaded: false,
  missionJSON: null,
  generatedPath: [],
  trail: [],
  telemetry: null,
  mapLayer: 'street',
  followVessel: true,
  manualLeft: false,
  manualRight: false,
  manualThrottle: 0.5,

  setBackendUrl:  (backendUrl)  => set({ backendUrl }),
  setWsStatus:    (wsStatus: ConnectionStatus) => set({ wsStatus }),

  loadMissionJSON: (json: PlannerMissionJSON) =>
    set({ missionJSON: json, missionLoaded: true, trail: [], generatedPath: [] }),

  setGeneratedPath: (generatedPath: LatLng[]) => set({ generatedPath }),

  applyTelemetry: (frame: TelemetryFrame) =>
    set((s) => {
      const pos: LatLng = { lat: frame.lat, lng: frame.lng };
      const trail = [...s.trail, pos].slice(-TRAIL_MAX);
      return { telemetry: frame, trail };
    }),

  clearMission: () =>
    set({ missionLoaded: false, missionJSON: null, generatedPath: [], trail: [], telemetry: null }),

  setMapLayer:    (mapLayer: MapLayer) => set({ mapLayer }),
  setFollowVessel: (followVessel: boolean) => set({ followVessel }),

  setManualMotor: (left, right, throttle?) =>
    set((s) => ({
      manualLeft:     left,
      manualRight:    right,
      manualThrottle: throttle ?? s.manualThrottle,
    })),
}));