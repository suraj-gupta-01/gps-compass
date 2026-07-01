// Ported from backend/utils/geo.py — haversine, bearing, heading_error
// Exact math preserved; projection constants copied from Python source.

const R = 6371000;

export function haversine(lat1: number, lng1: number, lat2: number, lng2: number): number {
  // Port of utils/geo.py::haversine
  const p1 = (lat1 * Math.PI) / 180;
  const p2 = (lat2 * Math.PI) / 180;
  const dp = ((lat2 - lat1) * Math.PI) / 180;
  const dl = ((lng2 - lng1) * Math.PI) / 180;
  const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(Math.max(0.0, 1 - a)));
}

export function bearing(lat1: number, lng1: number, lat2: number, lng2: number): number {
  // Port of utils/geo.py::bearing
  const p1 = (lat1 * Math.PI) / 180;
  const p2 = (lat2 * Math.PI) / 180;
  const dl = ((lng2 - lng1) * Math.PI) / 180;
  const y = Math.sin(dl) * Math.cos(p2);
  const x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

export function headingError(current: number, required: number): number {
  // Port of utils/geo.py::heading_error
  const err = ((required - current + 180) % 360) - 180;
  return err;
}

// Projection constants — copied exactly from kinematics.py / lawnmower.py
export const METERS_PER_DEG_LAT = 110540.0;
export function metersPerDegLng(lat: number): number {
  return 111320.0 * Math.cos((lat * Math.PI) / 180);
}
