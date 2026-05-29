import { useState } from 'react';
import { useDashboardStore } from '../store/dashboardStore';
import {
  enterManual, exitManual,
  sendMotorCmd, manualStop,
} from '../services/websocket';

interface Props { isManual: boolean; }

export function ManualControl({ isManual }: Props) {
  const store = useDashboardStore();
  const { backendUrl, manualLeft, manualRight, manualThrottle } = store;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function safe(fn: () => Promise<void>) {
    setBusy(true); setError(null);
    try { await fn(); }
    catch (e: any) { setError(e.message ?? 'Error'); }
    finally { setBusy(false); }
  }

  async function handleEnterManual() {
    await safe(async () => {
      await enterManual(backendUrl);
    });
  }

  async function handleExitManual() {
    await safe(async () => {
      await manualStop(backendUrl);  // stop motors first
      const res = await exitManual(backendUrl);
      store.setManualMotor(false, false);
    });
  }

  async function setMotor(left: boolean, right: boolean) {
    store.setManualMotor(left, right);
    await safe(async () => {
      await sendMotorCmd(backendUrl, left, right, manualThrottle);
    });
  }

  async function handleStop() {
    store.setManualMotor(false, false);
    await safe(async () => { await manualStop(backendUrl); });
  }

  async function handleThrottleChange(t: number) {
    store.setManualMotor(manualLeft, manualRight, t);
    if (isManual && (manualLeft || manualRight)) {
      await safe(async () => {
        await sendMotorCmd(backendUrl, manualLeft, manualRight, t);
      });
    }
  }

  // Derive steering label from local motor state
  function steeringLabel(): string {
    if (!manualLeft && !manualRight) return 'STOPPED';
    if (manualLeft && manualRight)   return 'FORWARD';
    if (manualLeft)                  return 'TURN RIGHT';
    return 'TURN LEFT';
  }
  const steerColor = !manualLeft && !manualRight ? '#6b7280'
    : manualLeft && manualRight ? '#00ff88' : '#00d4ff';

  return (
    <div className="flex flex-col gap-3">

      {/* Mode toggle */}
      <div className="flex gap-2">
        <button
          onClick={handleEnterManual}
          disabled={isManual || busy}
          className="flex-1 py-2 rounded text-xs font-mono font-bold uppercase tracking-wider transition-all disabled:opacity-40 disabled:cursor-not-allowed"
          style={{
            background: !isManual ? 'rgba(255,59,92,0.15)' : 'rgba(255,255,255,0.04)',
            color: !isManual ? '#ff3b5c' : '#6b7280',
            border: !isManual ? '1px solid rgba(255,59,92,0.4)' : '1px solid rgba(255,255,255,0.08)',
          }}
        >⚡ Manual</button>
        <button
          onClick={handleExitManual}
          disabled={!isManual || busy}
          className="flex-1 py-2 rounded text-xs font-mono font-bold uppercase tracking-wider transition-all disabled:opacity-40 disabled:cursor-not-allowed"
          style={{
            background: isManual ? 'rgba(0,212,255,0.15)' : 'rgba(255,255,255,0.04)',
            color: isManual ? '#00d4ff' : '#6b7280',
            border: isManual ? '1px solid rgba(0,212,255,0.4)' : '1px solid rgba(255,255,255,0.08)',
          }}
        >⬡ Return Auto</button>
      </div>

      {/* Active mode badge */}
      <div
        className="flex items-center gap-2 px-3 py-2 rounded"
        style={{
          background: isManual ? 'rgba(255,59,92,0.10)' : 'rgba(0,212,255,0.08)',
          border: isManual ? '1px solid rgba(255,59,92,0.3)' : '1px solid rgba(0,212,255,0.2)',
        }}
      >
        <div
          className="w-2 h-2 rounded-full"
          style={{
            background: isManual ? '#ff3b5c' : '#00d4ff',
            boxShadow: isManual ? '0 0 6px #ff3b5c' : '0 0 6px #00d4ff',
            animation: isManual ? 'pulse 1s infinite' : 'none',
          }}
        />
        <span className="text-xs font-mono font-bold tracking-widest uppercase"
          style={{ color: isManual ? '#ff3b5c' : '#00d4ff' }}>
          {isManual ? 'MANUAL OVERRIDE ACTIVE' : 'AUTO MODE'}
        </span>
      </div>

      {/* Motor controls — only active in manual */}
      <div className={`flex flex-col gap-2 transition-opacity ${isManual ? 'opacity-100' : 'opacity-30 pointer-events-none'}`}>
        <div className="section-label">Motor Controls</div>

        {/* D-pad style layout */}
        <div className="grid grid-cols-3 gap-1.5">
          <div />
          {/* Forward */}
          <MotorBtn
            label="▲ FWD"
            active={manualLeft && manualRight}
            onClick={() => setMotor(true, true)}
            color="#00ff88"
          />
          <div />
          {/* Turn left */}
          <MotorBtn
            label="◀ LEFT"
            active={!manualLeft && manualRight}
            onClick={() => setMotor(false, true)}
            color="#00d4ff"
          />
          {/* Stop */}
          <MotorBtn
            label="■ STOP"
            active={!manualLeft && !manualRight}
            onClick={handleStop}
            color="#ff3b5c"
          />
          {/* Turn right */}
          <MotorBtn
            label="RIGHT ▶"
            active={manualLeft && !manualRight}
            onClick={() => setMotor(true, false)}
            color="#00d4ff"
          />
          <div /><div /><div />
        </div>

        {/* Steering label */}
        <div
          className="text-center py-1.5 rounded font-mono text-xs font-bold tracking-widest uppercase"
          style={{
            background: `${steerColor}12`,
            border: `1px solid ${steerColor}30`,
            color: steerColor,
          }}
        >{steeringLabel()}</div>

        {/* Throttle */}
        <div>
          <div className="flex justify-between text-xs font-mono mb-1">
            <span className="text-gray-600">Throttle</span>
            <span className="text-accent-amber font-bold">{Math.round(manualThrottle * 100)}%</span>
          </div>
          <input
            type="range" min={0} max={100} step={5}
            value={Math.round(manualThrottle * 100)}
            onChange={(e) => handleThrottleChange(Number(e.target.value) / 100)}
            className="w-full" style={{ accentColor: '#ffb800' }}
          />
        </div>

        {/* Motor LED indicators */}
        <div className="flex justify-around mt-1">
          <MotorLed label="LEFT"  on={manualLeft}  />
          <MotorLed label="RIGHT" on={manualRight} />
        </div>
      </div>

      {/* Return-to-auto note */}
      {isManual && (
        <div className="text-xs font-mono text-gray-600 text-center leading-relaxed"
          style={{ borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: 8 }}>
          On exit: sequencer re-anchors to nearest<br/>remaining path point automatically.
        </div>
      )}

      {/* Error display */}
      {error && (
        <div className="text-xs font-mono text-accent-red px-2 py-1 rounded"
          style={{ background: 'rgba(255,59,92,0.08)', border: '1px solid rgba(255,59,92,0.2)' }}>
          {error}
        </div>
      )}
    </div>
  );
}

function MotorBtn({ label, active, onClick, color }: {
  label: string; active: boolean; onClick: () => void; color: string;
}) {
  return (
    <button
      onClick={onClick}
      className="py-2 rounded text-xs font-mono font-bold transition-all active:scale-95"
      style={{
        background: active ? `${color}25` : 'rgba(255,255,255,0.04)',
        border: active ? `1px solid ${color}70` : '1px solid rgba(255,255,255,0.08)',
        color: active ? color : '#6b7280',
        boxShadow: active ? `0 0 8px ${color}40` : 'none',
      }}
    >{label}</button>
  );
}

function MotorLed({ label, on }: { label: string; on: boolean }) {
  return (
    <div className="flex flex-col items-center gap-1">
      <div
        className="w-8 h-8 rounded-full flex items-center justify-center"
        style={{
          background: on ? 'rgba(0,255,136,0.2)' : 'rgba(255,255,255,0.04)',
          border: on ? '2px solid #00ff88' : '2px solid rgba(255,255,255,0.1)',
          boxShadow: on ? '0 0 14px #00ff8870' : 'none',
        }}
      >
        <div className="w-4 h-4 rounded-full"
          style={{ background: on ? '#00ff88' : 'rgba(255,255,255,0.06)' }} />
      </div>
      <span className="text-xs font-mono"
        style={{ color: on ? '#00ff88' : 'rgba(255,255,255,0.2)' }}>{label}</span>
    </div>
  );
}