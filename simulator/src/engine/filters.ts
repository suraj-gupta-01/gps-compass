// Ported from backend/telemetry/filters.py — CompassFilter, GpsFilter
// Exact math preserved: EMA on unit circle, max-jump outlier rejection.

/**
 * Port of CompassFilter — EMA on unit circle components.
 */
export class CompassFilter {
  private alpha: number;
  private sinVal = 0;
  private cosVal = 0;
  private initialized = false;

  constructor(alpha = 0.3) {
    this.alpha = alpha;
  }

  update(headingDeg: number): number {
    // Port of CompassFilter.update()
    const rad = ((headingDeg % 360) * Math.PI) / 180;
    const s = Math.sin(rad);
    const c = Math.cos(rad);

    if (!this.initialized) {
      this.sinVal = s;
      this.cosVal = c;
      this.initialized = true;
    } else {
      this.sinVal = this.alpha * s + (1 - this.alpha) * this.sinVal;
      this.cosVal = this.alpha * c + (1 - this.alpha) * this.cosVal;
    }

    return (Math.atan2(this.sinVal, this.cosVal) * 180 / Math.PI + 360) % 360;
  }

  reset(): void {
    this.sinVal = 0;
    this.cosVal = 0;
    this.initialized = false;
  }
}

/**
 * Port of GpsFilter — max-jump outlier rejection.
 */
export class GpsFilter {
  private maxJumpM: number;
  private lat = 0;
  private lng = 0;
  private initialized = false;
  private rejects = 0;
  private static MAX_CONSECUTIVE_REJECTS = 5;

  constructor(maxJumpM = 25.0) {
    this.maxJumpM = maxJumpM;
  }

  private haversine(lat1: number, lng1: number, lat2: number, lng2: number): number {
    // Port of GpsFilter._haversine
    const R = 6371000.0;
    const p1 = (lat1 * Math.PI) / 180;
    const p2 = (lat2 * Math.PI) / 180;
    const dp = ((lat2 - lat1) * Math.PI) / 180;
    const dl = ((lng2 - lng1) * Math.PI) / 180;
    const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(Math.max(0.0, 1 - a)));
  }

  update(lat: number, lng: number): { lat: number; lng: number; accepted: boolean } {
    // Port of GpsFilter.update()
    if (!isFinite(lat) || !isFinite(lng) || lat < -90 || lat > 90 || lng < -180 || lng > 180) {
      if (this.initialized) return { lat: this.lat, lng: this.lng, accepted: false };
      return { lat: 0, lng: 0, accepted: false };
    }

    if (!this.initialized) {
      this.lat = lat;
      this.lng = lng;
      this.rejects = 0;
      this.initialized = true;
      return { lat, lng, accepted: true };
    }

    const dist = this.haversine(this.lat, this.lng, lat, lng);

    if (dist <= this.maxJumpM || this.rejects >= GpsFilter.MAX_CONSECUTIVE_REJECTS) {
      this.lat = lat;
      this.lng = lng;
      this.rejects = 0;
      return { lat, lng, accepted: true };
    } else {
      this.rejects++;
      return { lat: this.lat, lng: this.lng, accepted: false };
    }
  }

  reset(): void {
    this.lat = 0;
    this.lng = 0;
    this.initialized = false;
    this.rejects = 0;
  }
}
