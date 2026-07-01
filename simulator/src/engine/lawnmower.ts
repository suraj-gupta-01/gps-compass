// Ported from backend/coverage/lawnmower.py — generate_lawnmower
// Exact math preserved: same projection, same scanline intersection logic.

import { METERS_PER_DEG_LAT, metersPerDegLng } from './geo';
import type { LatLng } from '../types';

function toLocal(vertices: LatLng[]): [number, number][] {
  // Port of lawnmower.py::_to_local
  const o = vertices[0];
  const mPerLng = metersPerDegLng(o.lat);
  return vertices.map(v => [
    (v.lng - o.lng) * mPerLng,
    (v.lat - o.lat) * METERS_PER_DEG_LAT,
  ]);
}

function fromLocal(x: number, y: number, origin: LatLng): LatLng {
  // Port of lawnmower.py::_from_local
  const mPerLng = metersPerDegLng(origin.lat);
  return {
    lat: origin.lat + y / METERS_PER_DEG_LAT,
    lng: origin.lng + x / mPerLng,
  };
}

function scanlineIntersections(poly: [number, number][], scanY: number): number[] {
  // Port of lawnmower.py::_scanline_intersections
  const xs: number[] = [];
  const n = poly.length;
  for (let i = 0; i < n; i++) {
    const [ax, ay] = poly[i];
    const [bx, by] = poly[(i + 1) % n];
    const lo = Math.min(ay, by);
    const hi = Math.max(ay, by);
    if (scanY < lo || scanY >= hi) continue;
    const t = (scanY - ay) / (by - ay);
    xs.push(ax + t * (bx - ax));
  }
  return xs.sort((a, b) => a - b);
}

/**
 * Port of lawnmower.py::generate_lawnmower
 * Boustrophedon coverage path for an arbitrary polygon.
 */
export function generateLawnmower(polygon: LatLng[], sweepWidthM: number): LatLng[] {
  if (polygon.length < 3) return [];
  const sw = Math.max(sweepWidthM, 1.0);
  const origin = polygon[0];
  const poly = toLocal(polygon);

  const ys = poly.map(p => p[1]);
  const yMin = Math.min(...ys);
  const yMax = Math.max(...ys);

  const result: LatLng[] = [];
  let row = 0;
  let y = yMin + sw / 2;

  while (y <= yMax) {
    const xs = scanlineIntersections(poly, y);
    for (let p = 0; p < xs.length - 1; p += 2) {
      const xl = xs[p];
      const xr = xs[p + 1];
      const left = fromLocal(xl, y, origin);
      const right = fromLocal(xr, y, origin);
      if (row % 2 === 0) {
        result.push(left, right);
      } else {
        result.push(right, left);
      }
      row++;
    }
    y += sw;
  }
  return result;
}
