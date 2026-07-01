// What it does: centralizes AeroSim HTTP/WebSocket calls to the real backend,
// including mission upload, commands, telemetry WS, and passive HIL feeds.
// Imports from: shared simulator/backend TypeScript contracts.
// Behavior: existing backend bridge functions are preserved; HIL helpers are
// additive and the sendSim* functions are fire-and-forget for 10 Hz ticks.

import type { BackendTelemetryFrame, LatLng, PathPoint, SimStatusResponse } from '../types';
import type { SyntheticPerceptionFrame } from '../engine/SimEngine';

function normalizeBaseUrl(url: string): string {
  const trimmed = (url || '').trim();
  if (!trimmed) return 'http://localhost:8000';
  return trimmed.replace(/\/$/, '');
}

function toWsUrl(baseUrl: string): string {
  const normalized = normalizeBaseUrl(baseUrl);
  return `${normalized.replace(/^http/, 'ws')}/ws/telemetry`;
}

let ws: WebSocket | null = null;
let shouldReconnect = false;

export interface BackendMissionPayload {
  groundControl: { id: string; position: { lat: number; lng: number } } | null;
  mission: Array<
    | { type: 'waypoint_path'; id: string; label: string; points: { lat: number; lng: number }[] }
    | { type: 'area_coverage'; id: string; label: string; sweepWidth: number; polygon: { lat: number; lng: number }[] }
  >;
}

export function buildMissionPayload(opts: {
  geofence: LatLng[];
  gcsPoint: LatLng | null;
  sweepWidth: number;
  generatedPath: PathPoint[];
}): BackendMissionPayload {
  const mission: BackendMissionPayload['mission'] = [];

  if (opts.geofence.length >= 3) {
    mission.push({
      type: 'area_coverage',
      id: 'sim-coverage',
      label: 'Coverage',
      sweepWidth: opts.sweepWidth,
      polygon: opts.geofence.map((p) => ({ lat: p.lat, lng: p.lng })),
    });
  } else if (opts.generatedPath.length > 0) {
    mission.push({
      type: 'waypoint_path',
      id: 'sim-waypoints',
      label: 'Waypoints',
      points: opts.generatedPath.map((p) => ({ lat: p.lat, lng: p.lng })),
    });
  }

  return {
    groundControl: opts.gcsPoint
      ? { id: 'gcs', position: { lat: opts.gcsPoint.lat, lng: opts.gcsPoint.lng } }
      : null,
    mission,
  };
}

export async function uploadMissionToBackend(backendUrl: string, mission: BackendMissionPayload): Promise<void> {
  const res = await fetch(`${normalizeBaseUrl(backendUrl)}/api/mission`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(mission),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail?.detail ?? `HTTP ${res.status}`);
  }
}

export async function getSimStatus(backendUrl: string): Promise<SimStatusResponse> {
  const res = await fetch(`${normalizeBaseUrl(backendUrl)}/api/sim/status`);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail?.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

export function sendSimTelemetry(
  backendUrl: string,
  body: { lat: number; lng: number; heading: number },
): void {
  fetch(`${normalizeBaseUrl(backendUrl)}/api/sim/telemetry`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).catch((error) => console.warn('HIL telemetry feed failed', error));
}

export function sendSimPerception(backendUrl: string, frame: SyntheticPerceptionFrame): void {
  fetch(`${normalizeBaseUrl(backendUrl)}/api/sim/perception`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(frame),
  }).catch((error) => console.warn('HIL perception feed failed', error));
}

export async function sendBackendCommand(backendUrl: string, command: string): Promise<void> {
  const res = await fetch(`${normalizeBaseUrl(backendUrl)}/api/command`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ command }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail?.detail ?? `HTTP ${res.status}`);
  }
}

export function connectBackendTelemetry(
  backendUrl: string,
  onTelemetry: (frame: BackendTelemetryFrame) => void,
  onStatus: (connected: boolean, message: string) => void,
): () => void {
  shouldReconnect = false;
  ws?.close();
  ws = null;

  try {
    const url = toWsUrl(backendUrl);
    onStatus(false, 'connecting');
    ws = new WebSocket(url);
    ws.onopen = () => onStatus(true, 'connected');
    ws.onmessage = (evt) => {
      try {
        const frame = JSON.parse(evt.data) as BackendTelemetryFrame;
        onTelemetry(frame);
      } catch (error) {
        console.warn('Backend telemetry parse error', error);
      }
    };
    ws.onerror = () => onStatus(false, 'connection error');
    ws.onclose = () => onStatus(false, 'disconnected');
  } catch (error) {
    console.warn('Backend websocket setup failed', error);
    onStatus(false, 'connection error');
  }

  return () => {
    shouldReconnect = false;
    ws?.close();
    ws = null;
  };
}

export function connectHILTelemetry(
  backendUrl: string,
  onTelemetry: (frame: BackendTelemetryFrame) => void,
  onStatus: (connected: boolean, message: string) => void,
): () => void {
  return connectBackendTelemetry(backendUrl, onTelemetry, onStatus);
}

export function disconnectBackendTelemetry(): void {
  shouldReconnect = false;
  ws?.close();
  ws = null;
}
