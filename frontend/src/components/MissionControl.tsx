import { useRef } from 'react';
import { useDashboardStore } from '../store/dashboardStore';
import { uploadMission, sendCommand } from '../services/websocket';
import { ManualControl } from './ManualControl';
import type { PlannerMissionJSON, MissionUploadResponse } from '../types';

type ConnectionStatus = 'disconnected' | 'connecting' | 'connected' | 'error';
const statusColors: Record<ConnectionStatus, string> = {
  disconnected: '#6b7280', connecting: '#ffb800',
  connected: '#00ff88',   error: '#ff3b5c',
};

export function MissionControl() {
  const store = useDashboardStore();
  const fileRef = useRef<HTMLInputElement>(null);
  const { wsStatus, backendUrl, missionJSON, telemetry } = store;

  const mode     = telemetry?.mode ?? 'auto';
  const isManual = mode === 'manual';
  const navState = telemetry?.nav_state ?? 'idle';
  const isRunning = !isManual && (navState === 'navigating' || navState === 'coverage' || navState === 'returning');
  const isPaused  = !isManual && navState === 'paused';
  const isDone    = navState === 'completed';

  async function handleFileLoad(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    try {
      const json: PlannerMissionJSON = JSON.parse(text);
      store.loadMissionJSON(json);
      const res: MissionUploadResponse = await uploadMission(backendUrl, json);
      if (res.ok) store.setGeneratedPath(res.generated_path ?? []);
    } catch (err) { console.error('Mission load/upload failed:', err); }
    e.target.value = '';
  }

  async function cmd(command: string) {
    try { await sendCommand(backendUrl, command); } catch (e) { console.error(e); }
  }

  return (
    <div className="flex flex-col gap-4">

      {/* Connection */}
      <section>
        <div className="section-label mb-2">Backend Connection</div>
        <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)' }}
          className="rounded-lg p-3 space-y-2">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full" style={{ background: statusColors[wsStatus] }} />
            <span className="text-xs font-mono uppercase tracking-widest"
              style={{ color: statusColors[wsStatus] }}>{wsStatus}</span>
          </div>
          <input
            type="text" value={backendUrl}
            onChange={(e) => store.setBackendUrl(e.target.value)}
            placeholder="ws://localhost:8000"
            className="w-full bg-transparent text-xs font-mono text-gray-400 outline-none px-2 py-1 rounded"
            style={{ border: '1px solid rgba(255,255,255,0.08)' }}
          />
        </div>
      </section>

      {/* Manual override — always visible */}
      <section>
        <div className="section-label mb-2">Operating Mode</div>
        <ManualControl isManual={isManual} />
      </section>

      {/* Mission file */}
      <section>
        <div className="section-label mb-2">Mission File</div>
        <div className="flex gap-2">
          <button onClick={() => fileRef.current?.click()} className="btn-primary flex-1"
            disabled={isManual}>
            ↑ Load Mission JSON
          </button>
          {missionJSON && (
            <button onClick={store.clearMission} className="btn-danger px-3" disabled={isManual}>✕</button>
          )}
        </div>
        <input ref={fileRef} type="file" accept=".json" className="hidden" onChange={handleFileLoad} />
        {missionJSON && (
          <div className="mt-1.5 text-xs font-mono text-gray-600">
            {missionJSON.mission.length} segment{missionJSON.mission.length !== 1 ? 's' : ''} loaded
          </div>
        )}
      </section>

      {/* Autonomous execution — greyed out in manual mode */}
      <section className={isManual ? 'opacity-40 pointer-events-none' : ''}>
        <div className="section-label mb-2">Mission Execution</div>
        <div className="flex flex-col gap-2">
          {!isRunning && !isPaused && !isDone && (
            <button
              onClick={() => cmd('start')}
              disabled={!missionJSON || wsStatus !== 'connected'}
              className="btn-primary w-full disabled:opacity-30 disabled:cursor-not-allowed"
            >▶ Start Mission</button>
          )}
          {isRunning && (
            <button onClick={() => cmd('pause')} className="btn-secondary w-full">⏸ Pause</button>
          )}
          {isPaused && (
            <button onClick={() => cmd('resume')} className="btn-primary w-full">▶ Resume</button>
          )}
          {isDone && (
            <div className="text-center py-2 text-accent-cyan text-xs font-mono tracking-widest">
              ✓ Mission Complete
            </div>
          )}
          {(isRunning || isPaused || isDone) && (
            <button onClick={() => cmd('reset')} className="btn-danger w-full">↺ Reset</button>
          )}
        </div>
      </section>

      {/* Map */}
      <section>
        <div className="section-label mb-2">Map</div>
        <div className="flex gap-2">
          {(['street','satellite'] as const).map((l) => (
            <button key={l} onClick={() => store.setMapLayer(l)}
              className="flex-1 py-1.5 rounded text-xs font-mono uppercase tracking-wider transition-all"
              style={{
                background: store.mapLayer === l ? 'rgba(0,212,255,0.15)' : 'rgba(255,255,255,0.04)',
                color: store.mapLayer === l ? '#00d4ff' : '#6b7280',
                border: store.mapLayer === l ? '1px solid rgba(0,212,255,0.5)' : '1px solid rgba(255,255,255,0.08)',
              }}>{l}</button>
          ))}
        </div>
        <label className="flex items-center gap-2 mt-2 cursor-pointer">
          <input type="checkbox" checked={store.followVessel}
            onChange={(e) => store.setFollowVessel(e.target.checked)}
            className="accent-cyan-400" />
          <span className="text-xs font-mono text-gray-500">Follow vessel</span>
        </label>
      </section>

      {/* Nav state badge */}
      {telemetry && (
        <section>
          <div className="section-label mb-2">Nav State</div>
          <NavStateBadge state={telemetry.nav_state} mode={mode} />
        </section>
      )}
    </div>
  );
}

function NavStateBadge({ state, mode }: { state: string; mode: string }) {
  const cfg: Record<string, { color: string }> = {
    idle:       { color: '#6b7280' },
    navigating: { color: '#00d4ff' },
    coverage:   { color: '#9b59ff' },
    returning:  { color: '#ffb800' },
    completed:  { color: '#00ff88' },
    paused:     { color: '#ffb800' },
    manual:     { color: '#ff3b5c' },
  };
  const { color } = cfg[mode === 'manual' ? 'manual' : state] ?? cfg.idle;
  const label = mode === 'manual' ? 'MANUAL' : state.replace(/_/g, ' ').toUpperCase();
  return (
    <div className="flex items-center gap-2 px-3 py-2 rounded"
      style={{ background: `${color}12`, border: `1px solid ${color}33` }}>
      <div className="w-2 h-2 rounded-full animate-pulse" style={{ background: color }} />
      <span className="text-xs font-mono font-bold uppercase tracking-widest" style={{ color }}>
        {label}
      </span>
    </div>
  );
}