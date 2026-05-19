import { useDashboardStore } from '../store/dashboardStore';
import { CompassWidget } from './CompassWidget';
import { MotorIndicator } from './MotorIndicator';
import { formatDistance, formatLatLng } from '../utils/geo';

function TCard({ label, value, sub, accent }: { label: string; value: string; sub?: string; accent?: string }) {
  return (
    <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)' }}
      className="rounded-lg px-3 py-2.5">
      <div className="text-gray-600 text-xs font-mono uppercase tracking-wider mb-1">{label}</div>
      <div className="font-mono text-base font-bold" style={{ color: accent ?? 'white' }}>{value}</div>
      {sub && <div className="text-gray-600 text-xs font-mono mt-0.5">{sub}</div>}
    </div>
  );
}

export function TelemetryPanel() {
  const { telemetry, trail } = useDashboardStore();

  if (!telemetry) {
    return (
      <div className="flex-1 flex items-center justify-center text-gray-600 text-xs font-mono text-center p-8">
        Waiting for telemetry...<br/>
        <span className="text-gray-700 mt-1 block">Connect backend and upload mission</span>
      </div>
    );
  }

  const t = telemetry;
  const progressPct = Math.round(t.mission_progress * 100);

  return (
    <div className="flex flex-col gap-4 overflow-y-auto">
      {/* Position */}
      <section>
        <div className="section-label mb-2">Position</div>
        <div className="grid grid-cols-2 gap-2">
          <TCard label="Latitude"  value={t.lat.toFixed(6)} accent="#00d4ff" />
          <TCard label="Longitude" value={t.lng.toFixed(6)} accent="#00d4ff" />
        </div>
      </section>

      {/* Navigation */}
      <section>
        <div className="section-label mb-2">Navigation</div>
        <div className="grid grid-cols-2 gap-2">
          <TCard label="Heading"     value={`${String(Math.round(t.heading)).padStart(3,'0')}°`} accent="#00ff88" />
          <TCard label="Required"    value={`${String(Math.round(t.required_heading)).padStart(3,'0')}°`} accent="#ffb800" />
          <TCard label="Distance"    value={formatDistance(t.distance_to_target)} />
          <TCard label="Error"       value={`${t.heading_error > 0 ? '+' : ''}${Math.round(t.heading_error)}°`}
            accent={Math.abs(t.heading_error) > 20 ? '#ff3b5c' : Math.abs(t.heading_error) > 8 ? '#ffb800' : '#00ff88'} />
        </div>
      </section>

      {/* Compass */}
      <section>
        <div className="section-label mb-2">Compass</div>
        <CompassWidget heading={t.heading} required={t.required_heading} error={t.heading_error} />
      </section>

      {/* Motors */}
      <section>
        <div className="section-label mb-2">Steering</div>
        <MotorIndicator motor={t.motor} steering={t.steering} />
      </section>

      {/* Mission progress */}
      <section>
        <div className="section-label mb-2">Mission Progress</div>
        <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)' }}
          className="rounded-lg p-3 space-y-2">
          <div className="flex justify-between text-xs font-mono">
            <span className="text-gray-400">{t.active_segment_label}</span>
            <span className="text-accent-cyan">{progressPct}%</span>
          </div>
          <div className="h-2 rounded-full overflow-hidden" style={{ background: 'rgba(255,255,255,0.06)' }}>
            <div
              className="h-full rounded-full transition-all duration-500"
              style={{ width: `${progressPct}%`, background: 'linear-gradient(90deg,#00d4ff,#00ff88)' }}
            />
          </div>
          <div className="flex justify-between text-xs font-mono text-gray-600">
            <span>PT {t.active_segment_index + 1}/{t.total_path_points}</span>
            <span>TRAIL {trail.length} PTS</span>
          </div>
        </div>
      </section>

      {/* Source badge */}
      <div className="flex items-center gap-2">
        <div className="w-1.5 h-1.5 rounded-full" style={{ background: t.source === 'uart' ? '#00ff88' : '#ffb800' }} />
        <span className="text-xs font-mono text-gray-600 uppercase tracking-widest">
          {t.source === 'uart' ? 'UART / Live GPS' : 'Mock Telemetry'}
        </span>
      </div>
    </div>
  );
}