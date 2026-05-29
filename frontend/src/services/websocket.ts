import { useDashboardStore } from '../store/dashboardStore';
import type { TelemetryFrame } from '../types';

let ws: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let shouldReconnect = true;

function clearReconnect() {
  if (reconnectTimer !== null) { clearTimeout(reconnectTimer); reconnectTimer = null; }
}

export function connectWebSocket() {
  clearReconnect();
  shouldReconnect = true;
  const store = useDashboardStore.getState();
  const url = store.backendUrl.replace(/^http/, 'ws') + '/ws/telemetry';
  store.setWsStatus('connecting');
  try {
    ws = new WebSocket(url);
  } catch {
    store.setWsStatus('error');
    scheduleReconnect();
    return;
  }
  ws.onopen    = () => useDashboardStore.getState().setWsStatus('connected');
  ws.onmessage = (evt) => {
    try {
      const frame: TelemetryFrame = JSON.parse(evt.data);
      useDashboardStore.getState().applyTelemetry(frame);
    } catch (e) { console.warn('WS parse error', e); }
  };
  ws.onerror = () => useDashboardStore.getState().setWsStatus('error');
  ws.onclose = () => {
    useDashboardStore.getState().setWsStatus('disconnected');
    if (shouldReconnect) scheduleReconnect();
  };
}

function scheduleReconnect() {
  clearReconnect();
  reconnectTimer = setTimeout(() => { if (shouldReconnect) connectWebSocket(); }, 3000);
}

export function disconnectWebSocket() {
  shouldReconnect = false;
  clearReconnect();
  ws?.close();
  ws = null;
}

function httpBase(backendUrl: string): string {
  return backendUrl.replace(/^ws/, 'http');
}

async function post(backendUrl: string, path: string, body?: object): Promise<any> {
  const res = await fetch(`${httpBase(backendUrl)}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail?.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

export async function uploadMission(backendUrl: string, json: object): Promise<any> {
  return post(backendUrl, '/api/mission', json);
}

export async function sendCommand(backendUrl: string, cmd: string, payload?: object): Promise<void> {
  await post(backendUrl, '/api/command', { command: cmd, ...payload });
}

// ── Manual mode ────────────────────────────────────────────────────────────
export async function enterManual(backendUrl: string): Promise<void> {
  await post(backendUrl, '/api/manual/enter');
}

export async function exitManual(backendUrl: string): Promise<any> {
  return post(backendUrl, '/api/manual/exit');
}

export async function sendMotorCmd(
  backendUrl: string,
  left: boolean,
  right: boolean,
  throttle: number,
): Promise<void> {
  await post(backendUrl, '/api/manual/motor', { left, right, throttle });
}

export async function manualStop(backendUrl: string): Promise<void> {
  await post(backendUrl, '/api/manual/stop');
}