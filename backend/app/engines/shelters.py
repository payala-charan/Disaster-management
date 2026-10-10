"""Shelter capacity calculations. Available capacity is always derived and never stored.

- available    = max(0, capacity - current_occupancy)
- utilization  = current_occupancy / capacity (0 when capacity is 0)
- approaching_threshold = utilization >= SHELTER_ALERT_THRESHOLD (default 0.85)
- accepting    = status 'open' and available > 0
- can_accommodate(n) = accepting and available >= n
- crossed_threshold: true only on the update that moves utilization from below the threshold to
  at/above it, so the capacity alert fires once rather than on every later update.
- nearest_with_capacity: nearest accepting shelter (haversine) able to take n people.
"""

from app.engines.geo import haversine_m
from app.models.schemas import Contract, GeoPoint, Shelter

DEFAULT_THRESHOLD = 0.85


class ShelterState(Contract):
    shelter_id: str
    available: int
    utilization: float
    approaching_threshold: bool
    accepting: bool


def available(s: Shelter) -> int:
    return max(0, s.capacity - s.current_occupancy)


def utilization(s: Shelter) -> float:
    return s.current_occupancy / s.capacity if s.capacity else 0.0


def shelter_state(s: Shelter, threshold: float = DEFAULT_THRESHOLD) -> ShelterState:
    return ShelterState(
        shelter_id=s.id,
        available=available(s),
        utilization=round(utilization(s), 4),
        approaching_threshold=utilization(s) >= threshold,
        accepting=s.status == "open" and available(s) > 0,
    )


def can_accommodate(s: Shelter, people: int) -> bool:
    return s.status == "open" and available(s) >= people


def crossed_threshold(old_occupancy: int, new_occupancy: int, capacity: int,
                      threshold: float = DEFAULT_THRESHOLD) -> bool:
    if capacity <= 0:
        return False
    return old_occupancy / capacity < threshold <= new_occupancy / capacity


def nearest_with_capacity(location: GeoPoint, shelters: list[Shelter], people: int = 1) -> tuple[Shelter, float] | None:
    """(shelter, distance in metres) or None if no shelter can take `people`."""
    options = [(s, haversine_m(location, s.location)) for s in shelters if can_accommodate(s, people)]
    return min(options, key=lambda o: (o[1], o[0].id)) if options else None
