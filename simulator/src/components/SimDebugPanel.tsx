import { useSimStore } from '../store/simStore';

const STATE_COLORS: Record<string, string> = {
  navigate: '#00ff88',
  avoid_obstacle: '#ff3b5c',
  track_trash: '#ffb800',
  collect_trash: '#ffb800',
  return_to_path: '#9b59ff',
};

export function SimDebugPanel() {
  const ds = useSimStore(s => s.debugState);
  const obstacles = useSimStore(s => s.obstacles);
  const trash = useSimStore(s => s.trash);

  if (!ds) {
    return (
      <div className="text-xs font-mono text-gray-600 text-center p-4">
        No telemetry yet. Start simulation.
      </div>
    );
  }

  const stateColor = STATE_COLORS[ds.behavior_state] ?? '#6b7280';

  return (
    <div className="flex flex-col gap-3 text-xs font-mono overflow-y-auto">

      {/* Behavior state */}
      <section>
        <div className="section-label mb-1">Behavior State</div>
        <div className="flex items-center gap-2 px-2 py-1.5 rounded"
          style={{ background: `${stateColor}15`, border: `1px solid ${stateColor}40` }}>
          <div className="w-2 h-2 rounded-full animate-pulse" style={{ background: stateColor }} />
          <span className="font-bold uppercase tracking-widest" style={{ color: stateColor }}>
            {ds.behavior_state.replace(/_/g, ' ')}
          </span>
        </div>
      </section>

      {/* Position */}
      <section>
        <div className="section-label mb-1">Position</div>
        <div className="grid grid-cols-2 gap-1">
          <KV k="Lat" v={ds.lat.toFixed(7)} />
          <KV k="Lng" v={ds.lng.toFixed(7)} />
          <KV k="Heading" v={`${ds.heading.toFixed(1)}°`} />
          <KV k="Speed" v={`${ds.speed.toFixed(3)} m/s`} />
        </div>
      </section>

      {/* Control */}
      <section>
        <div className="section-label mb-1">Control</div>
        <div className="grid grid-cols-2 gap-1">
          <KV k="ω cmd" v={`${ds.omega_cmd.toFixed(2)}°/s`} />
          <KV k="Speed cmd" v={`${ds.speed_cmd.toFixed(3)} m/s`} />
          <KV k="Path idx" v={`${ds.path_index}/${ds.total_path_points}`} />
          <KV k="Tick" v={`${ds.tick}`} />
        </div>
      </section>

      {/* Lookahead */}
      <section>
        <div className="section-label mb-1">Lookahead</div>
        <div className="grid grid-cols-2 gap-1">
          <KV k="Lat" v={ds.lookahead_lat.toFixed(7)} />
          <KV k="Lng" v={ds.lookahead_lng.toFixed(7)} />
        </div>
      </section>

      {/* Steering */}
      <section>
        <div className="section-label mb-1">Steering</div>
        <div className="grid grid-cols-3 gap-1">
          <MotorLED label="L" on={ds.motor_cmd.left_on} />
          <div className="flex items-center justify-center">
            <span className="text-gray-500 uppercase">{ds.motor_cmd.steering_label}</span>
          </div>
          <MotorLED label="R" on={ds.motor_cmd.right_on} />
        </div>
      </section>

      {/* Observations */}
      <section>
        <div className="section-label mb-1">Observations</div>
        {ds.obstacle_obs ? (
          <div className="text-red-400">
            OBSTACLE: cx={ds.obstacle_obs.image_cx_norm.toFixed(3)}, w={ds.obstacle_obs.width_frac.toFixed(3)}
          </div>
        ) : (
          <div className="text-gray-700">No obstacle</div>
        )}
        {ds.trash_obs ? (
          <div className="text-amber-400">
            TRASH: cx={ds.trash_obs.image_cx_norm.toFixed(3)}, area={ds.trash_obs.area_frac.toFixed(4)}
          </div>
        ) : (
          <div className="text-gray-700">No trash</div>
        )}
      </section>

      {/* Objects */}
      <section>
        <div className="section-label mb-1">Placed Objects</div>
        <div className="text-gray-500">
          {obstacles.length} obstacle{obstacles.length !== 1 ? 's' : ''},{' '}
          {trash.length} trash
        </div>
      </section>

      {/* Nav state */}
      <section>
        <KV k="Nav state" v={ds.nav_state} />
        <KV k="GPS" v={ds.gps_accepted ? 'OK' : 'REJECTED'} />
      </section>
    </div>
  );
}

function KV({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-gray-600">{k}</span>
      <span className="text-white">{v}</span>
    </div>
  );
}

function MotorLED({ label, on }: { label: string; on: boolean }) {
  return (
    <div className="flex flex-col items-center gap-0.5">
      <div className="w-6 h-6 rounded-full flex items-center justify-center"
        style={{
          background: on ? 'rgba(0,255,136,0.2)' : 'rgba(255,255,255,0.04)',
          border: on ? '2px solid #00ff88' : '2px solid rgba(255,255,255,0.1)',
          boxShadow: on ? '0 0 10px #00ff8866' : 'none',
        }}>
        <div className="w-3 h-3 rounded-full"
          style={{ background: on ? '#00ff88' : 'rgba(255,255,255,0.06)' }} />
      </div>
      <span style={{ color: on ? '#00ff88' : 'rgba(255,255,255,0.2)' }}>{label}</span>
    </div>
  );
}
