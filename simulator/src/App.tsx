import { useEffect, useRef } from 'react';
import { SimMap } from './components/SimMap';
import { SimControls } from './components/SimControls';
import { SimToolbar } from './components/SimToolbar';
import { SimDebugPanel } from './components/SimDebugPanel';
import { useSimStore } from './store/simStore';

function Header() {
  const ds = useSimStore(s => s.debugState);
  const paused = useSimStore(s => s.simPaused);

  return (
    <header className="flex items-center justify-between px-4 py-2 flex-shrink-0"
      style={{ background: 'rgba(13,17,23,0.98)', borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
      <div className="flex items-center gap-2">
        <svg viewBox="0 0 24 24" width="24" height="24">
          <polygon points="12,2 18,20 12,16 6,20" fill="#00d4ff" />
        </svg>
        <div>
          <div className="text-white font-bold text-sm" style={{ fontFamily: 'monospace' }}>AeroSim</div>
          <div className="text-gray-600 text-xs" style={{ fontFamily: 'monospace' }}>Navigation Simulator</div>
        </div>
      </div>
      <div className="flex items-center gap-4 text-xs font-mono">
        {ds && (
          <>
            <span className="text-gray-500">TICK <span className="text-white">{ds.tick}</span></span>
            <span className="text-gray-500">HDG <span className="text-accent-green">{ds.heading.toFixed(1)}°</span></span>
            <span className="text-gray-500">SPD <span className="text-white">{ds.speed.toFixed(2)}</span></span>
          </>
        )}
        <span className="px-2 py-0.5 rounded" style={{
          background: paused ? 'rgba(255,184,0,0.15)' : 'rgba(0,255,136,0.15)',
          color: paused ? '#ffb800' : '#00ff88',
          border: `1px solid ${paused ? 'rgba(255,184,0,0.4)' : 'rgba(0,255,136,0.4)'}`,
        }}>
          {paused ? 'PAUSED' : 'RUNNING'}
        </span>
      </div>
    </header>
  );
}

export default function App() {
  const doTick = useSimStore(s => s.doTick);
  const refreshDebug = useSimStore(s => s.refreshDebug);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    // 10 Hz simulation loop
    intervalRef.current = setInterval(() => {
      doTick();
      refreshDebug();
    }, 100);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [doTick, refreshDebug]);

  return (
    <div className="w-screen h-screen flex flex-col overflow-hidden" style={{ background: '#080c10' }}>
      <Header />
      <div className="flex flex-1 overflow-hidden">
        {/* Left sidebar — Controls + Toolbar */}
        <aside className="w-64 flex-shrink-0 flex flex-col overflow-y-auto"
          style={{ background: 'rgba(13,17,23,0.97)', borderRight: '1px solid rgba(255,255,255,0.05)' }}>
          <div className="px-3 py-3 flex-1">
            <SimToolbar />
            <div className="my-3 border-t border-white/5" />
            <SimControls />
          </div>
        </aside>

        {/* Map — centre */}
        <main className="flex-1 relative overflow-hidden">
          <SimMap />
        </main>

        {/* Right sidebar — Debug */}
        <aside className="w-64 flex-shrink-0 flex flex-col overflow-y-auto"
          style={{ background: 'rgba(13,17,23,0.97)', borderLeft: '1px solid rgba(255,255,255,0.05)' }}>
          <div className="px-3 py-3">
            <div className="section-label mb-2">Debug Panel</div>
            <SimDebugPanel />
          </div>
        </aside>
      </div>
    </div>
  );
}
