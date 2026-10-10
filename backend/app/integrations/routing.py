"""Travel distance/time between points: OSRM public server, with a labelled estimate as fallback.

Live:  GET {OSRM_URL}/table/v1/driving/{lng,lat;...}?sources=..&destinations=..&annotations=duration,distance
       one request for a whole resources x incidents matrix (the public demo server allows ~100 coordinates).
       A single pair uses /route/v1/driving/{lng},{lat};{lng},{lat}?overview=false.
Fallback (ROUTING_MODE=simulation, timeout, error, or a null cell): haversine distance x 1.3 at 30 km/h,
       labelled source="estimated" (engines/geo.py).

IMPORTANT: OSRM knows nothing about flood closures. Travel times say how far a place is, never
whether it is reachable. Reachability comes only from our road registry (engines/access.py), which
always overrides OSRM. This module never constructs alternative routes.
"""

import logging
from collections.abc import Sequence

import httpx

from app.core.config import Settings, get_settings
from app.engines.allocation import TravelInfo
from app.engines.geo import estimate_travel
from app.models.schemas import GeoPoint

log = logging.getLogger(__name__)
MAX_TABLE_COORDS = 100


def estimated(a: GeoPoint, b: GeoPoint) -> TravelInfo:
    km, minutes = estimate_travel(a, b)
    return TravelInfo(distance_km=round(km, 2), minutes=round(minutes, 1), source="estimated")


def _coords(points: Sequence[GeoPoint]) -> str:
    return ";".join(f"{p.lng:.6f},{p.lat:.6f}" for p in points)  # OSRM wants lng,lat


class RoutingService:
    def __init__(self, settings: Settings | None = None, http: httpx.Client | None = None):
        self.settings = settings or get_settings()
        self.http = http or httpx.Client(timeout=self.settings.http_timeout_s)
        self.last_error: str | None = None  # for the agent trace

    @property
    def live(self) -> bool:
        return self.settings.routing_mode == "live"

    def route(self, origin: GeoPoint, dest: GeoPoint) -> TravelInfo:
        if not self.live:
            return estimated(origin, dest)
        try:
            resp = self.http.get(f"{self.settings.osrm_url}/route/v1/driving/{_coords([origin, dest])}",
                                 params={"overview": "false"})
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "Ok" or not data.get("routes"):
                raise ValueError(f"OSRM code {data.get('code')}")
            r = data["routes"][0]
            self.last_error = None
            return TravelInfo(distance_km=round(r["distance"] / 1000, 2), minutes=round(r["duration"] / 60, 1), source="osrm")
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"OSRM unavailable ({type(exc).__name__}); estimated"
            log.warning(self.last_error)
            return estimated(origin, dest)

    def table(self, origins: dict[str, GeoPoint], destinations: dict[str, GeoPoint]) -> dict[tuple[str, str], TravelInfo]:
        """Travel info for every (origin_id, destination_id) pair. Never raises; missing cells are estimated."""
        o_ids, d_ids = list(origins), list(destinations)
        result = {(o, d): estimated(origins[o], destinations[d]) for o in o_ids for d in d_ids}
        if not self.live or not o_ids or not d_ids:
            return result
        if len(o_ids) + len(d_ids) > MAX_TABLE_COORDS:
            self.last_error = f"Too many points for OSRM table ({len(o_ids) + len(d_ids)}); estimated"
            return result
        points = [origins[o] for o in o_ids] + [destinations[d] for d in d_ids]
        try:
            resp = self.http.get(
                f"{self.settings.osrm_url}/table/v1/driving/{_coords(points)}",
                params={"sources": ";".join(str(i) for i in range(len(o_ids))),
                        "destinations": ";".join(str(len(o_ids) + j) for j in range(len(d_ids))),
                        "annotations": "duration,distance"},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "Ok":
                raise ValueError(f"OSRM code {data.get('code')}")
            for i, o in enumerate(o_ids):
                for j, d in enumerate(d_ids):
                    secs, metres = data["durations"][i][j], data["distances"][i][j]
                    if secs is not None and metres is not None:  # null = OSRM found no route: keep estimate
                        result[(o, d)] = TravelInfo(distance_km=round(metres / 1000, 2), minutes=round(secs / 60, 1), source="osrm")
            self.last_error = None
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"OSRM unavailable ({type(exc).__name__}); estimated"
            log.warning(self.last_error)
        return result
