import { useEffect, useRef } from 'react';
import { MapContainer, TileLayer, Polyline, Polygon, Marker, useMap } from 'react-leaflet';
import { useDashboardStore } from '../store/dashboardStore';
import { createVesselIcon, createTargetIcon, createGCSIcon, createWaypointDot } from '../utils/icons';
import type { PlannerWaypointPathSegment, PlannerAreaCoverageSegment, LatLng } from '../types';

const PATH_COLORS = ['#00d4ff','#00b8d4','#0099b3','#007a8f'];
function pathColor(i: number) { return PATH_COLORS[i % PATH_COLORS.length]; }

function VesselFollower() {
  const map = useMap();
  const { telemetry, followVessel } = useDashboardStore();
  useEffect(() => {
    if (followVessel && telemetry) {
      map.setView([telemetry.lat, telemetry.lng], map.getZoom(), { animate: true });
    }
  }, [telemetry?.lat, telemetry?.lng, followVessel]);
  return null;
}

function LayerSwitcher() {
  const mapLayer = useDashboardStore((s) => s.mapLayer);
  const url = mapLayer === 'satellite'
    ? 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
    : 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
  const attr = mapLayer === 'satellite' ? '© Esri' : '© OpenStreetMap © CARTO';
  return <TileLayer url={url} attribution={attr} />;
}

export function MissionMap() {
  const { missionJSON, generatedPath, telemetry, trail } = useDashboardStore();

  const gc = missionJSON?.groundControl;
  const segments = missionJSON?.mission ?? [];

  const defaultCenter: [number, number] = gc
    ? [gc.position.lat, gc.position.lng]
    : [37.7749, -122.4194];

  // Split segments by type
  const waypointSegs = segments.filter((s): s is PlannerWaypointPathSegment => s.type === 'waypoint_path');
  const coverageSegs = segments.filter((s): s is PlannerAreaCoverageSegment => s.type === 'area_coverage');

  const vesselIcon  = telemetry ? createVesselIcon(telemetry.heading) : null;
  const targetIcon  = createTargetIcon();
  const gcsIcon     = createGCSIcon();

  return (
    <MapContainer
      center={defaultCenter}
      zoom={15}
      className="w-full h-full"
      zoomControl={false}
      style={{ background: '#080c10' }}
    >
      <LayerSwitcher />
      <VesselFollower />

      {/* GCS marker */}
      {gc && (
        <Marker position={[gc.position.lat, gc.position.lng]} icon={gcsIcon} />
      )}

      {/* Waypoint path segments */}
      {waypointSegs.map((seg, si) => {
        const pts = seg.points.map((p) => [p.lat, p.lng] as [number, number]);
        return (
          <span key={seg.id}>
            {pts.length >= 2 && (
              <>
                <Polyline positions={pts} pathOptions={{ color: pathColor(si), weight: 5, opacity: 0.12 }} />
                <Polyline positions={pts} pathOptions={{ color: pathColor(si), weight: 2, opacity: 0.8, dashArray: '8 5' }} />
              </>
            )}
            {seg.points.map((p, pi) => (
              <Marker key={pi} position={[p.lat, p.lng]} icon={createWaypointDot(pi + 1)} />
            ))}
          </span>
        );
      })}

      {/* Coverage polygons */}
      {coverageSegs.map((seg) => (
        <Polygon
          key={seg.id}
          positions={seg.polygon.map((p) => [p.lat, p.lng] as [number, number])}
          pathOptions={{ color: '#9b59ff', fillColor: '#9b59ff', fillOpacity: 0.12, weight: 1.5, dashArray: '6 4' }}
        />
      ))}

      {/* Generated coverage sweep path */}
      {generatedPath.length >= 2 && (
        <>
          <Polyline
            positions={generatedPath.map((p) => [p.lat, p.lng] as [number, number])}
            pathOptions={{ color: '#9b59ff', weight: 4, opacity: 0.1 }}
          />
          <Polyline
            positions={generatedPath.map((p) => [p.lat, p.lng] as [number, number])}
            pathOptions={{ color: '#c084fc', weight: 1.5, opacity: 0.7, dashArray: '4 4' }}
          />
        </>
      )}

      {/* Vessel trail */}
      {trail.length >= 2 && (
        <Polyline
          positions={trail.map((p) => [p.lat, p.lng] as [number, number])}
          pathOptions={{ color: '#00ff88', weight: 2, opacity: 0.5 }}
        />
      )}

      {/* Target marker */}
      {telemetry && (
        <Marker position={[telemetry.target_lat, telemetry.target_lng]} icon={targetIcon} />
      )}

      {/* Vessel marker */}
      {telemetry && vesselIcon && (
        <Marker position={[telemetry.lat, telemetry.lng]} icon={vesselIcon} zIndexOffset={1000} />
      )}
    </MapContainer>
  );
}