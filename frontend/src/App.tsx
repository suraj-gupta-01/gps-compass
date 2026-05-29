import { useWebSocket } from './hooks/useWebSocket';
import { MissionMap } from './components/MissionMap';
import { TelemetryPanel } from './components/TelemetryPanel';
import { MissionControl } from './components/MissionControl';
import { useDashboardStore } from './store/dashboardStore';

function Header() {
  const { wsStatus, telemetry } = useDashboardStore();
  const dotColor = wsStatus === 'connected' ? '#00ff88' : wsStatus === 'connecting' ? '#ffb800' : wsStatus === 'error' ? '#ff3b5c' : '#6b7280';

  return (
    <header
      className="flex items-center justify-between px-6 py-3 flex-shrink-0"
      style={{ background:'rgba(13,17,23,0.98)', borderBottom:'1px solid rgba(255,255,255,0.05)', zIndex:100 }}
    >
      <div className="flex items-center gap-3">
        <svg viewBox="0 0 28 28" width="28" height="28">
          <rect width="28" height="28" rx="5" fill="#080c10"/>
          <polygon points="14,3 21,23 14,18 7,23" fill="#00ff88"/>
          <circle cx="14" cy="14" r="2.5" fill="#080c10"/>
        </svg>
        <div>
          <div className="text-white font-bold text-sm tracking-tight" style={{fontFamily:'"Space Mono",monospace'}}>
            AeroNav
          </div>
          <div className="text-gray-600 text-xs font-mono tracking-widest uppercase">Execution Dashboard</div>
        </div>
      </div>

      <div className="flex items-center gap-6">
        {telemetry && (
          <>
            <HeaderStat label="LAT" value={telemetry.lat.toFixed(5)} />
            <HeaderStat label="LNG" value={telemetry.lng.toFixed(5)} />
            <HeaderStat label="HDG" value={`${String(Math.round(telemetry.heading)).padStart(3,'0')}°`} color="#00ff88" />
            <HeaderStat label="SPD" value="—" />
          </>
        )}
        <div className="flex items-center gap-2 px-3 py-1.5 rounded" style={{background:'rgba(255,255,255,0.04)', border:'1px solid rgba(255,255,255,0.08)'}}>
          <div className="w-1.5 h-1.5 rounded-full" style={{background: dotColor}} />
          <span className="text-xs font-mono uppercase tracking-widest text-gray-500">{wsStatus}</span>
        </div>
      </div>
    </header>
  );
}

function HeaderStat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="text-center">
      <div className="text-gray-700 text-xs font-mono tracking-widest">{label}</div>
      <div className="font-mono text-xs font-bold" style={{ color: color ?? 'white' }}>{value}</div>
    </div>
  );
}

export default function App() {
  useWebSocket();

  return (
    <div className="w-screen h-screen flex flex-col overflow-hidden" style={{background:'#080c10'}}>
      <Header />

      <div className="flex flex-1 overflow-hidden">
        {/* Left sidebar — Mission Control */}
        <aside
          className="w-72 flex-shrink-0 flex flex-col overflow-y-auto"
          style={{ background:'rgba(13,17,23,0.97)', borderRight:'1px solid rgba(255,255,255,0.05)' }}
        >
          <div className="px-4 py-4 flex-1">
            <MissionControl />
          </div>
          <div className="px-4 py-3 text-xs font-mono text-gray-700" style={{borderTop:'1px solid rgba(255,255,255,0.05)'}}>
            AeroNav Execution v1.0
          </div>
        </aside>

        {/* Map — centre, takes remaining width */}
        <main className="flex-1 relative overflow-hidden">
          <MissionMap />
        </main>

        {/* Right sidebar — Telemetry */}
        <aside
          className="w-72 flex-shrink-0 flex flex-col overflow-y-auto"
          style={{ background:'rgba(13,17,23,0.97)', borderLeft:'1px solid rgba(255,255,255,0.05)' }}
        >
          <div className="px-4 py-4">
            <div className="section-label mb-3">Live Telemetry</div>
            <TelemetryPanel />
          </div>
        </aside>
      </div>
    </div>
  );
}