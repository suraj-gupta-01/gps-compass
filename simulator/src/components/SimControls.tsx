import { useSimStore } from '../store/simStore';
import { generateLawnmower } from '../engine/lawnmower';
import type { PathPoint } from '../types';

export function SimControls() {
  const store = useSimStore();
  const { vesselCfg, camera, sweepWidth, geofence, gcsPoint, generatedPath, simPaused, backendUrl, useBackend, backendStatus, backendTelemetry, simSpeed } = store;

  function handleGeneratePath() {
    if (geofence.length < 3) return;
    const sweepPts = generateLawnmower(geofence, sweepWidth);
    if (sweepPts.length === 0) return;

    const path: PathPoint[] = [];

    // Add GCS as first point
    if (gcsPoint) {
      path.push({ lat: gcsPoint.lat, lng: gcsPoint.lng, segment_label: 'GCS', segment_type: 'gcs', acceptance_radius_m: 1.0 });
    }

    // Add sweep points
    for (const pt of sweepPts) {
      path.push({ lat: pt.lat, lng: pt.lng, segment_label: 'coverage', segment_type: 'area_coverage', acceptance_radius_m: 0.5 });
    }

    // Return to GCS
    if (gcsPoint) {
      path.push({ lat: gcsPoint.lat, lng: gcsPoint.lng, segment_label: 'Return GCS', segment_type: 'gcs', acceptance_radius_m: 1.0 });
    }

    store.loadMission(path);
  }

  return (
    <div className="flex flex-col gap-4">

      {/* Vessel Configuration */}
      <section>
        <div className="section-label mb-2">Vessel Config</div>
        <div className="grid grid-cols-2 gap-2">
          <Field label="Cruise (m/s)" value={vesselCfg.cruise_speed_mps}
            onChange={v => store.setVesselCfg({ cruise_speed_mps: v })} />
          <Field label="Max Speed" value={vesselCfg.max_speed_mps}
            onChange={v => store.setVesselCfg({ max_speed_mps: v })} />
          <Field label="Max Turn (°/s)" value={vesselCfg.max_turn_rate_dps}
            onChange={v => store.setVesselCfg({ max_turn_rate_dps: v })} />
          <Field label="Lookahead (m)" value={vesselCfg.lookahead_m}
            onChange={v => store.setVesselCfg({ lookahead_m: v })} />
          <Field label="Heading Kp" value={vesselCfg.heading_kp}
            onChange={v => store.setVesselCfg({ heading_kp: v })} />
          <Field label="WP Radius (m)" value={vesselCfg.waypoint_radius_m}
            onChange={v => store.setVesselCfg({ waypoint_radius_m: v })} />
          <Field label="Turn Speed %" value={vesselCfg.turn_speed_factor * 100}
            onChange={v => store.setVesselCfg({ turn_speed_factor: v / 100 })} />
          <Field label="Speed Tau (s)" value={vesselCfg.speed_tau}
            onChange={v => store.setVesselCfg({ speed_tau: v })} />
        </div>
      </section>

      {/* Simulation Speed */}
      <section>
        <div className="section-label mb-2">Simulation Speed</div>
        <div className="flex items-center gap-2">
          <input type="range" min="0.1" max="5" step="0.1" value={simSpeed}
            onChange={(e) => store.setSimSpeed(parseFloat(e.target.value))} />
          <div className="text-xs font-mono text-gray-400">{simSpeed.toFixed(1)}x</div>
        </div>
      </section>

      {/* Camera FOV */}
      <section>
        <div className="section-label mb-2">Camera FOV</div>
        <div className="grid grid-cols-2 gap-2">
          <Field label="H-FOV (°)" value={camera.hfov_deg}
            onChange={v => store.setCamera({ hfov_deg: v })} />
          <Field label="Max Range (m)" value={camera.max_range_m}
            onChange={v => store.setCamera({ max_range_m: v })} />
        </div>
      </section>

      {/* Sweep Width */}
      <section>
        <div className="section-label mb-2">Coverage</div>
        <Field label="Sweep Width (m)" value={sweepWidth}
          onChange={v => store.setSweepWidth(v)} />
        <button
          onClick={handleGeneratePath}
          disabled={geofence.length < 3}
          className="btn-primary w-full mt-2 disabled:opacity-30"
        >
          Generate Lawnmower Path
        </button>
      </section>

      {/* Backend bridge */}
      <section>
        <div className="section-label mb-2">Backend Bridge</div>
        <label className="flex items-center gap-2 text-xs font-mono text-gray-500 mb-2">
          <input
            type="checkbox"
            checked={useBackend}
            onChange={(e) => store.setUseBackend(e.target.checked)}
            className="accent-cyan-400"
          />
          Send simulator actions to backend
        </label>
        <input
          type="text"
          value={backendUrl}
          onChange={(e) => store.setBackendUrl(e.target.value)}
          placeholder="http://localhost:8000"
          className="w-full bg-surface-800 text-xs font-mono text-white px-2 py-1 rounded border border-white/10 outline-none focus:border-accent-cyan/50"
        />
        <div className="mt-2 text-xs font-mono text-gray-500">
          Status: <span className="text-white">{backendStatus}</span>
        </div>
        {backendTelemetry && (
          <div className="mt-2 text-[11px] font-mono text-gray-500">
            Last backend: {backendTelemetry.nav_state} • {backendTelemetry.mode}
          </div>
        )}
      </section>

      {/* Mission */}
      <section>
        <div className="section-label mb-2">Mission</div>
        <div className="text-xs font-mono text-gray-500 mb-2">
          {generatedPath.length} points loaded
        </div>
        <div className="flex gap-2">
          <button onClick={() => store.start()} disabled={!generatedPath.length || !simPaused}
            className="btn-primary flex-1 disabled:opacity-30">
            ▶ Start
          </button>
          <button onClick={() => store.pause()} disabled={simPaused}
            className="btn-secondary flex-1 disabled:opacity-30">
            ⏸ Pause
          </button>
          <button onClick={() => store.resume()} disabled={!simPaused || !generatedPath.length}
            className="btn-primary flex-1 disabled:opacity-30">
            ▶ Resume
          </button>
        </div>
        <button onClick={() => store.reset()} className="btn-danger w-full mt-2">
          ↺ Reset
        </button>
      </section>

      {/* Reset All */}
      <section>
        <button onClick={() => { store.clearGeofence(); store.clearObjects(); store.setGcsPoint(null!); }}
          className="btn-danger w-full">
          Clear All
        </button>
      </section>
    </div>
  );
}

function Field({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
  return (
    <label className="flex flex-col gap-0.5">
      <span className="text-xs font-mono text-gray-600">{label}</span>
      <input
        type="number"
        value={value}
        onChange={e => onChange(parseFloat(e.target.value) || 0)}
        step="any"
        className="w-full bg-surface-800 text-xs font-mono text-white px-2 py-1 rounded border border-white/10 outline-none focus:border-accent-cyan/50"
      />
    </label>
  );
}
