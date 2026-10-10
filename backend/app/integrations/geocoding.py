"""Resolve where an incident is. Order of preference:

1. Explicit coordinates in the report (e.g. an IoT sensor or field-team payload) -> provenance "reported".
2. Zone gazetteer (data/seed/zones.json): an explicit zone name, or a gazetteer alias found in the text
   ("river street" -> Zone D). The zone centre is an approximation -> provenance "inferred".
3. Optional Nominatim lookup (GEOCODING_NOMINATIM=true): at most 1 request/second, identifying
   User-Agent, provenance "inferred". Off by default because the demo district is fictional.
4. Otherwise the location stays unknown (None) with "Location unknown: confirm incident location".
   Never a guessed point.
"""

import logging
import re
import threading
import time

import httpx

from app.core.config import Settings, get_settings
from app.db.seed import load_seed
from app.models.schemas import Contract, GeoPoint, Provenance
from app.models.seed_data import Zone

log = logging.getLogger(__name__)
LOCATION_UNKNOWN = "Location unknown: confirm incident location"


class GeocodeResult(Contract):
    zone: str | None
    location: GeoPoint | None
    provenance: Provenance | None
    method: str  # coordinates | zone_name | zone_alias | nominatim | unresolved
    verification_needed: list[str]


class Geocoder:
    def __init__(self, settings: Settings | None = None, zones: list[Zone] | None = None, http: httpx.Client | None = None):
        self.settings = settings or get_settings()
        self.zones = zones if zones is not None else load_seed().gazetteer.zones
        self.http = http
        self._lock = threading.Lock()
        self._last_call = 0.0

    def zone_by_name(self, name: str | None) -> Zone | None:
        if not name:
            return None
        key = name.strip().lower()
        return next((z for z in self.zones if z.name.lower() == key or key in z.aliases), None)

    def zone_in_text(self, text: str) -> Zone | None:
        lowered = text.lower()
        for z in self.zones:
            for term in [z.name.lower(), *z.aliases]:
                if re.search(rf"\b{re.escape(term)}\b", lowered):
                    return z
        return None

    def zone_for_point(self, p: GeoPoint) -> Zone | None:
        """Nearest gazetteer zone centre within ~3 km (so coordinates can still be grouped by zone)."""
        from app.engines.geo import haversine_m

        best = min(self.zones, key=lambda z: haversine_m(p, z.center), default=None)
        return best if best and haversine_m(p, best.center) <= 3_000 else None

    def _nominatim(self, query: str) -> GeoPoint | None:
        if not self.settings.geocoding_nominatim or not query:
            return None
        with self._lock:  # usage policy: max 1 request per second
            wait = 1.0 - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
        try:
            http = self.http or httpx.Client(timeout=self.settings.http_timeout_s)
            resp = http.get(self.settings.nominatim_url, params={"q": query, "format": "jsonv2", "limit": 1},
                            headers={"User-Agent": self.settings.nominatim_user_agent})
            resp.raise_for_status()
            hits = resp.json()
            return GeoPoint(lat=float(hits[0]["lat"]), lng=float(hits[0]["lon"])) if hits else None
        except Exception as exc:  # noqa: BLE001
            log.warning("Nominatim unavailable (%s)", type(exc).__name__)
            return None

    def resolve(self, zone: str | None = None, text: str = "", lat: float | None = None,
                lng: float | None = None, location_text: str | None = None) -> GeocodeResult:
        if lat is not None and lng is not None and -90 <= lat <= 90 and -180 <= lng <= 180:
            point = GeoPoint(lat=lat, lng=lng)
            z = self.zone_by_name(zone) or self.zone_for_point(point)
            return GeocodeResult(zone=z.name if z else zone, location=point, provenance="reported",
                                 method="coordinates", verification_needed=[])

        z = self.zone_by_name(zone)
        method = "zone_name"
        if z is None:
            z = self.zone_in_text(" ".join(filter(None, [location_text, text])))
            method = "zone_alias"
        if z is not None:
            return GeocodeResult(zone=z.name, location=z.center, provenance="inferred", method=method,
                                 verification_needed=[f"Location approximated to {z.name} centre: confirm exact site"])

        point = self._nominatim(location_text or "")
        if point is not None:
            near = self.zone_for_point(point)
            return GeocodeResult(zone=near.name if near else zone, location=point, provenance="inferred",
                                 method="nominatim", verification_needed=["Geocoded location: confirm exact site"])

        return GeocodeResult(zone=zone, location=None, provenance=None, method="unresolved",
                             verification_needed=[LOCATION_UNKNOWN])
