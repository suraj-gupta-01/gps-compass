import { useSimStore } from '../store/simStore';
import type { ToolbarMode } from '../types';

const TOOLS: { mode: ToolbarMode; label: string; color: string }[] = [
  { mode: 'draw_geofence', label: 'Draw Geofence', color: '#9b59ff' },
  { mode: 'place_gcs', label: 'Place GCS', color: '#00ff88' },
  { mode: 'place_obstacle', label: 'Place Obstacle', color: '#ff3b5c' },
  { mode: 'place_trash', label: 'Place Trash', color: '#ffb800' },
  { mode: 'place_waypoint', label: 'Add Waypoint', color: '#00d4ff' },
];

export function SimToolbar() {
  const mode = useSimStore(s => s.toolbarMode);
  const setMode = useSimStore(s => s.setToolbarMode);

  return (
    <div className="flex flex-col gap-1.5">
      <div className="section-label mb-1">Tools</div>
      {TOOLS.map(t => (
        <button
          key={t.mode}
          onClick={() => setMode(mode === t.mode ? 'none' : t.mode)}
          className="py-1.5 px-2 rounded text-xs font-mono font-bold uppercase tracking-wider transition-all text-left"
          style={{
            background: mode === t.mode ? `${t.color}20` : 'rgba(255,255,255,0.03)',
            color: mode === t.mode ? t.color : '#6b7280',
            border: mode === t.mode ? `1px solid ${t.color}60` : '1px solid rgba(255,255,255,0.08)',
          }}
        >
          {t.label}
        </button>
      ))}
      {mode !== 'none' && (
        <div className="text-xs font-mono text-gray-600 text-center mt-1">
          Click on map to place
        </div>
      )}
    </div>
  );
}
