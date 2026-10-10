"""Distance and travel-time estimates (pure math, no network).

- Haversine great-circle distance on a sphere of radius 6,371,008.8 m (mean Earth radius).
- Fallback travel estimate when OSRM is unavailable: road distance ~= straight line x 1.3
  (a common detour factor), driven at 30 km/h (cautious speed in flood conditions).
  The result is always labelled source="estimated" so it is never mistaken for routed data.
"""

import math

from app.models.schemas import GeoPoint

EARTH_RADIUS_M = 6_371_008.8
DETOUR_FACTOR = 1.3
FALLBACK_SPEED_KPH = 30.0


def haversine_m(a: GeoPoint, b: GeoPoint) -> float:
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlng = math.radians(b.lng - a.lng)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def estimate_travel(a: GeoPoint, b: GeoPoint) -> tuple[float, float]:
    """Return (distance_km, duration_min) using the documented fallback formula."""
    distance_km = haversine_m(a, b) / 1000 * DETOUR_FACTOR
    return distance_km, distance_km / FALLBACK_SPEED_KPH * 60
