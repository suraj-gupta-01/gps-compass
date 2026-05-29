import math
from typing import List, Tuple

def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distance in metres between two geographic points."""
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def bearing(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Initial bearing in degrees (0-360) from point 1 to point 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lng2 - lng1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1)*math.sin(p2) - math.sin(p1)*math.cos(p2)*math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360

def heading_error(current: float, required: float) -> float:
    """Signed shortest-path heading error in degrees (-180 to +180)."""
    err = (required - current + 180) % 360 - 180
    return err