"""Agent 4: Geospatial & Accessibility.

- Zone access from the road registry (engines/access.py). Known closures and AI-inferred issues are
  reported in separate lists and never treated as equivalent.
- Travel matrix from available resources to incidents (OSRM table, or labelled estimates). OSRM says
  how far, never whether a closed zone is reachable.
- Nearest shelters per incident with PostGIS ST_DistanceSphere.
- Isolated zones: zones whose known routes are all closed.
"""

from datetime import datetime
from typing import Any

from app.agents.base import Deps, StepRecorder, agent
from app.db import repository as repo
from app.engines.access import ZoneAccess, assess_zone_access
from app.engines.allocation import TravelInfo
from app.engines.models import IncidentFacts, RoadFacts
from app.graph.state import travel_key
from app.models.schemas import AccessAnalysis, RouteInfo, TravelEstimate

ACCESS_STATUS = {"open": "accessible", "partially_blocked": "partially_accessible",
                 "closed": "inaccessible_by_road", "unknown": "unknown"}


def build_access_analysis(inc: IncidentFacts, access: ZoneAccess, roads: list[RoadFacts],
                          travel: dict[tuple[str, str], TravelInfo], base_of: dict[str, str], now: datetime) -> AccessAnalysis:
    """Access picture for one incident (shared by this agent and GET /api/access/{incident_id}).
    Travel estimates are the fastest unit per base; known/reported routes and inferred issues stay separate."""
    zone_roads = [r for r in roads if r.serves_zone == inc.zone]
    best_by_base: dict[str, TravelEstimate] = {}
    for (rid, iid), t in travel.items():
        if iid != inc.id or rid not in base_of:
            continue
        base = base_of[rid]
        if base not in best_by_base or t.minutes < best_by_base[base].duration_min:
            best_by_base[base] = TravelEstimate(from_id=base, from_name=base, distance_km=t.distance_km,
                                                duration_min=t.minutes, source=t.source)
    verification = list(access.verification_needed)
    if inc.location is None:
        verification.append("Location unknown: travel and shelter distances unavailable")
    route = lambda r: RouteInfo(road_id=r.id, name=r.name, status=r.status, provenance=r.provenance, note=r.note)  # noqa: E731
    return AccessAnalysis(
        incident_id=inc.id, zone=inc.zone, access_status=ACCESS_STATUS[access.status],
        known_routes=[route(r) for r in zone_roads if r.provenance != "inferred"],
        inferred_issues=[route(r) for r in zone_roads if r.provenance == "inferred"],
        travel_estimates=sorted(best_by_base.values(), key=lambda t: t.duration_min),
        verification_needed=verification, analyzed_at=now,
    )


@agent("geospatial_accessibility")
def geospatial_accessibility(state: dict[str, Any], deps: Deps, rec: StepRecorder, db) -> dict[str, Any]:
    incidents = {k: IncidentFacts(**v) for k, v in state.get("incidents", {}).items()}
    zones = sorted({i.zone for i in incidents.values()})
    roads = rec.tool("road_registry.list", {}, lambda: repo.road_facts(db), lambda r: f"{len(r)} road segments")
    rec.input_summary = f"{len(incidents)} incident(s) in {len(zones)} zone(s); {len(roads)} registered roads"

    zone_access = {z: assess_zone_access(z, roads) for z in zones}

    available = [r for r in repo.resource_facts(db) if r.status == "available"]
    located = {i.id: i.location for i in incidents.values() if i.location is not None}
    travel = rec.tool(
        "osrm.table" if deps.routing.live else "travel.estimate_matrix",
        {"origins": len(available), "destinations": len(located)},
        lambda: deps.routing.table({r.id: r.location for r in available}, located),
        lambda t: (f"{sum(v.source == 'osrm' for v in t.values())} routed, "
                   f"{sum(v.source == 'estimated' for v in t.values())} estimated"
                   + (f" [{deps.routing.last_error}]" if deps.routing.last_error else "")),
    )

    analysis: dict[str, Any] = {}
    shelter_options: dict[str, list[dict[str, Any]]] = {}
    base_of = {r.id: r.base_name for r in available}
    for inc in incidents.values():
        if inc.location is not None:
            shelter_options[inc.id] = rec.tool("postgis.nearest_shelters", {"incident": inc.id},
                                               lambda: repo.nearest_shelters(db, inc.id),
                                               lambda s: ", ".join(f"{x['shelter_id']} {x['distance_m']} m" for x in s))
        analysis[inc.id] = build_access_analysis(inc, zone_access[inc.zone], roads, travel, base_of,
                                                 deps.clock()).model_dump(mode="json")

    isolated = [z for z, a in zone_access.items() if a.status == "closed"]
    rec.output_summary = ("Access: " + ", ".join(f"{z} {a.status} ({a.provenance})" for z, a in zone_access.items())
                          + f"; isolated by road: {isolated or 'none'}")
    return {
        "zone_access": {z: a.model_dump(mode="json") for z, a in zone_access.items()},
        "access_analysis": analysis,
        "travel": {travel_key(r, i): t.model_dump(mode="json") for (r, i), t in travel.items()},
        "shelter_options": shelter_options,
        "isolated_zones": isolated,
    }
