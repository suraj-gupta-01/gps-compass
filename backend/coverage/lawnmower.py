import math
from typing import List, Tuple, Dict

LatLng = Dict[str, float]

def _to_local(vertices: List[LatLng]) -> List[Tuple[float,float]]:
    """Project LatLng list to local metre plane anchored at vertices[0]."""
    o = vertices[0]
    mplat = 110540.0
    mplng = 111320.0 * math.cos(math.radians(o['lat']))
    return [(( v['lng'] - o['lng']) * mplng,
              (v['lat'] - o['lat']) * mplat) for v in vertices]

def _from_local(x: float, y: float, origin: LatLng) -> LatLng:
    mplat = 110540.0
    mplng = 111320.0 * math.cos(math.radians(origin['lat']))
    return {'lat': origin['lat'] + y / mplat, 'lng': origin['lng'] + x / mplng}

def _scanline_intersections(poly: List[Tuple[float,float]], scan_y: float) -> List[float]:
    xs = []
    n = len(poly)
    for i in range(n):
        ax, ay = poly[i]
        bx, by = poly[(i+1) % n]
        lo, hi = min(ay, by), max(ay, by)
        if scan_y < lo or scan_y >= hi:
            continue
        t = (scan_y - ay) / (by - ay)
        xs.append(ax + t * (bx - ax))
    return sorted(xs)

def generate_lawnmower(polygon: List[LatLng], sweep_width_m: float) -> List[LatLng]:
    """
    Boustrophedon (lawnmower) coverage path for an arbitrary polygon.
    Returns ordered LatLng waypoints covering the full interior.
    """
    if len(polygon) < 3:
        return []
    sw = max(sweep_width_m, 1.0)
    origin = polygon[0]
    poly = _to_local(polygon)

    ys = [p[1] for p in poly]
    y_min, y_max = min(ys), max(ys)

    result: List[LatLng] = []
    row = 0
    y = y_min + sw / 2
    while y <= y_max:
        xs = _scanline_intersections(poly, y)
        for p in range(0, len(xs) - 1, 2):
            xl, xr = xs[p], xs[p+1]
            left  = _from_local(xl, y, origin)
            right = _from_local(xr, y, origin)
            if row % 2 == 0:
                result.extend([left, right])
            else:
                result.extend([right, left])
            row += 1
        y += sw
    return result