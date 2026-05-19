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

  ws.onopen = () => {
    useDashboardStore.getState().setWsStatus('connected');
  };

  ws.onmessage = (evt) => {
    try {
      const frame: TelemetryFrame = JSON.parse(evt.data);
      useDashboardStore.getState().applyTelemetry(frame);
    } catch (e) {
      console.warn('WS parse error', e);
    }
  };

  ws.onerror = () => {
    useDashboardStore.getState().setWsStatus('error');
  };

  ws.onclose = () => {
    useDashboardStore.getState().setWsStatus('disconnected');
    if (shouldReconnect) scheduleReconnect();
  };
}

function scheduleReconnect() {
  clearReconnect();
  reconnectTimer = setTimeout(() => {
    if (shouldReconnect) connectWebSocket();
  }, 3000);
}

export function disconnectWebSocket() {
  shouldReconnect = false;
  clearReconnect();
  ws?.close();
  ws = null;
}

export async function uploadMission(backendUrl: string, json: object): Promise<any> {
  const base = backendUrl.replace(/^ws/, 'http');
  const res = await fetch(`${base}/api/mission`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(json),
  });
  if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
  return res.json();
}

export async function sendCommand(backendUrl: string, cmd: string, payload?: object): Promise<void> {
  const base = backendUrl.replace(/^ws/, 'http');
  await fetch(`${base}/api/command`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ command: cmd, ...payload }),
  });
}