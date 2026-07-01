import { useEffect, useRef, useCallback } from 'react';
import { MapContainer, TileLayer, Polyline, Polygon, Marker, Circle, useMap, useMapEvents } from 'react-leaflet';
import L from 'leaflet';
import { useSimStore } from '../store/simStore';
import {
  createVesselIcon, createObstacleIcon, createTrashIcon,
  createGCSIcon, createWaypointDot, createLookaheadIcon,
} from '../utils/icons';
import type { LatLng, ToolbarMode, PathPoint } from '../types';

const BEHAVIOR_COLORS: Record<string, string> = {
  navigate: '#00ff88',
  avoid_obstacle: '#ff3b5c',
  track_trash: '#ffb800',
  collect_trash: '#ffb800',
  return_to_path: '#9b59ff',
};

let nextId = 1;
function genId() { return `sim_${nextId++}`; }

function MapClickHandler() {
  const store = useSimStore();
  const mode = store.toolbarMode;

  useMapEvents({
    click(e) {
      const { lat, lng } = e.latlng;
      if (mode === 'place_obstacle') {
        store.addObstacle({ id: genId(), lat, lng, radius_m: 2.0, type: 'obstacle' });
        store.setToolbarMode('none');
      } else if (mode === 'place_trash') {
        store.addTrash({ id: genId(), lat, lng, area_cm2: 400, type: 'trash' });
        store.setToolbarMode('none');
      } else if (mode === 'place_gcs') {
        store.setGcsPoint({ lat, lng });
        store.setToolbarMode('none');
      } else if (mode === 'place_waypoint') {
        // Add waypoint to a simple path — append to generatedPath
        const path = [...store.generatedPath, {
          lat, lng,
          segment_label: 'waypoint',
          segment_type: 'waypoint_path',
          acceptance_radius_m: 4.0,
        }];
        store.setGeneratedPath(path);
      } else if (mode === 'draw_geofence') {
        store.addGeofencePoint({ lat, lng });
      }
    },
  });
  return null;
}

function VesselFollower() {
  const map = useMap();
  const vessel = useSimStore(s => s.debugState);
  useEffect(() => {
    if (vessel && !vessel.nav_state.includes('paused')) {
      map.setView([vessel.lat, vessel.lng], map.getZoom(), { animate: true });
    }
  }, [vessel?.lat, vessel?.lng, vessel?.nav_state]);
  return null;
}

function FOVCone() {
  const debug = useSimStore(s => s.debugState);
  const camera = useSimStore(s => s.camera);
  const vessel = useSimStore(s => s.debugState);
  if (!vessel) return null;

  const { lat, lng, heading } = vessel;
  const halfFov = camera.hfov_deg / 2;
  const range = camera.max_range_m;

  const toRad = (d: number) => (d * Math.PI) / 180;
  const mPerLat = 110540.0;
  const mPerLng = 111320.0 * Math.cos(toRad(lat));

  const leftBearing = heading - halfFov;
  const rightBearing = heading + halfFov;

  const leftEnd = {
    lat: lat + (range * Math.cos(toRad(leftBearing))) / mPerLat,
    lng: lng + (range * Math.sin(toRad(leftBearing))) / mPerLng,
  };
  const rightEnd = {
    lat: lat + (range * Math.cos(toRad(rightBearing))) / mPerLat,
    lng: lng + (range * Math.sin(toRad(rightBearing))) / mPerLng,
  };

  // Build a wedge using multiple points
  const positions: [number, number][] = [[lat, lng]];
  const steps = 12;
  for (let i = 0; i <= steps; i++) {
    const b = leftBearing + (rightBearing - leftBearing) * (i / steps);
    positions.push([
      lat + (range * Math.cos(toRad(b))) / mPerLat,
      lng + (range * Math.sin(toRad(b))) / mPerLng,
    ]);
  }
  positions.push([lat, lng]);

  return (
    <Polygon
      positions={positions}
      pathOptions={{ color: '#00d4ff', fillColor: '#00d4ff', fillOpacity: 0.08, weight: 1, opacity: 0.3 }}
    />
  );
}

export function SimMap() {
  const store = useSimStore();
  const { geofence, gcsPoint, generatedPath, obstacles, trash, debugState, toolbarMode } = store;
  const vessel = debugState;

  const vesselColor = vessel ? (BEHAVIOR_COLORS[vessel.behavior_state] ?? '#00ff88') : '#00ff88';

  const defaultCenter: [number, number] = gcsPoint
    ? [gcsPoint.lat, gcsPoint.lng]
    : [37.7749, -122.4194];

  const cursorStyle = toolbarMode !== 'none' ? 'crosshair' : '';

  return (
    <div style={{ cursor: cursorStyle }} className="w-full h-full">
      <MapContainer center={defaultCenter} zoom={16} className="w-full h-full" zoomControl={false}
        style={{ background: '#080c10' }}>
        <TileLayer
          url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
          attribution="© OpenStreetMap © CARTO"
        />
        <MapClickHandler />
        <VesselFollower />

        {/* GCS */}
        {gcsPoint && <Marker position={[gcsPoint.lat, gcsPoint.lng]} icon={createGCSIcon()} />}

        {/* Geofence polygon */}
        {geofence.length >= 3 && (
          <Polygon
            positions={geofence.map(p => [p.lat, p.lng] as [number, number])}
            pathOptions={{ color: '#9b59ff', fillColor: '#9b59ff', fillOpacity: 0.08, weight: 2, dashArray: '6 4' }}
          />
        )}

        {/* Geofence in-progress points */}
        {geofence.length > 0 && geofence.length < 3 && geofence.map((p, i) => (
          <Circle key={`gf-${i}`} center={[p.lat, p.lng]} radius={2}
            pathOptions={{ color: '#9b59ff', fillColor: '#9b59ff', fillOpacity: 1 }} />
        ))}

        {/* Generated coverage path */}
        {generatedPath.length >= 2 && (
          <>
            <Polyline
              positions={generatedPath.map(p => [p.lat, p.lng] as [number, number])}
              pathOptions={{ color: '#9b59ff', weight: 3, opacity: 0.15 }}
            />
            <Polyline
              positions={generatedPath.map(p => [p.lat, p.lng] as [number, number])}
              pathOptions={{ color: '#c084fc', weight: 1.5, opacity: 0.7, dashArray: '4 4' }}
            />
          </>
        )}

        {/* Waypoint dots on generated path */}
        {generatedPath.map((p, i) => (
          <Marker key={`wp-${i}`} position={[p.lat, p.lng]} icon={createWaypointDot(i + 1)} />
        ))}

        {/* Obstacles */}
        {obstacles.map(o => (
          <Marker key={o.id} position={[o.lat, o.lng]} icon={createObstacleIcon()}
            draggable={true}
            eventHandlers={{
              dragend(e) {
                const pos = e.target.getLatLng();
                store.updateObstacle(o.id, pos.lat, pos.lng, o.radius_m);
              },
            }}
          />
        ))}

        {/* Trash */}
        {trash.map(t => (
          <Marker key={t.id} position={[t.lat, t.lng]} icon={createTrashIcon()}
            draggable={true}
            eventHandlers={{
              dragend(e) {
                const pos = e.target.getLatLng();
                store.updateTrash(t.id, pos.lat, pos.lng);
              },
            }}
          />
        ))}

        {/* Camera FOV cone */}
        <FOVCone />

        {/* Lookahead point */}
        {vessel && (
          <Marker
            position={[vessel.lookahead_lat, vessel.lookahead_lng]}
            icon={createLookaheadIcon()}
          />
        )}

        {/* Vessel */}
        {vessel && (
          <Marker
            position={[vessel.lat, vessel.lng]}
            icon={createVesselIcon(vessel.heading, vesselColor)}
            zIndexOffset={1000}
          />
        )}
      </MapContainer>
    </div>
  );
}
