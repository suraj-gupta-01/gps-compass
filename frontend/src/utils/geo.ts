import type { LatLng } from '../types';

export function formatLatLng(pos: LatLng): string {
  return `${pos.lat.toFixed(5)}, ${pos.lng.toFixed(5)}`;
}

export function formatDistance(m: number): string {
  if (m < 1000) return `${Math.round(m)} m`;
  return `${(m / 1000).toFixed(2)} km`;
}

export function formatHeading(deg: number): string {
  return `${Math.round(deg).toString().padStart(3, '0')}°`;
}

export function compassPoint(deg: number): string {
  const pts = ['N','NE','E','SE','S','SW','W','NW'];
  return pts[Math.round(deg / 45) % 8];
}