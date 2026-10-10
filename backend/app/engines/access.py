"""Zone accessibility from the road registry.

Rules:
1. Only roads with provenance `known` determine a zone's access status. The BEST known status wins
   (open > partially_blocked > closed): one open known road means the zone is reachable.
2. If no known road has a definite status, `reported` roads (e.g. a field-team observation) are used,
   but the result keeps provenance `reported` and asks for verification.
3. If neither exists, status is `unknown` -> "Route verification required".
4. `inferred` roads (AI-derived potential problems) NEVER set the status. They are listed separately
   as `inferred_issues`, and an inferred alternative to a blocked zone produces a verification item.
   This keeps a known closure and an AI guess from ever looking equivalent.

OSRM travel times are a separate concern (integrations/routing.py). OSRM does not know about flood
closures, so this registry result always overrides it.
"""

from app.engines.models import RoadFacts
from app.models.schemas import Contract, Provenance, RoadStatus

ROUTE_VERIFICATION = "Route verification required"

# Lower rank = more accessible.
_RANK: dict[RoadStatus, int] = {"open": 0, "partially_blocked": 1, "closed": 2}


class ZoneAccess(Contract):
    zone: str
    status: RoadStatus             # open | partially_blocked | closed | unknown
    provenance: Provenance         # where the status came from
    deciding_road_id: str | None   # the road that determined the status
    deciding_road_name: str | None
    known_road_ids: list[str]
    inferred_issue_ids: list[str]
    verification_needed: list[str]

    @property
    def ground_access_possible(self) -> bool:
        """False only for a KNOWN closure of every known route."""
        return not (self.status == "closed" and self.provenance == "known")


def assess_zone_access(zone: str, roads: list[RoadFacts]) -> ZoneAccess:
    zone_roads = [r for r in roads if r.serves_zone == zone]
    known = [r for r in zone_roads if r.provenance == "known"]
    reported = [r for r in zone_roads if r.provenance == "reported"]
    inferred = [r for r in zone_roads if r.provenance == "inferred"]
    verification: list[str] = []

    def best(candidates: list[RoadFacts]) -> RoadFacts | None:
        definite = [r for r in candidates if r.status in _RANK]
        return min(definite, key=lambda r: (_RANK[r.status], r.id)) if definite else None

    deciding = best(known)
    provenance: Provenance = "known"
    if deciding is None:
        deciding = best(reported)
        provenance = "reported"
        if deciding is not None:
            verification.append(f"{deciding.name}: status '{deciding.status}' is reported, not confirmed. {ROUTE_VERIFICATION}")

    if deciding is None:
        status: RoadStatus = "unknown"
        provenance = "inferred" if inferred else "reported"
        verification.append(f"No confirmed route to {zone}. {ROUTE_VERIFICATION}")
    else:
        status = deciding.status

    if status in ("closed", "partially_blocked"):
        for r in inferred:
            verification.append(f"Alternative route {r.name} ({r.id}) is unconfirmed. {ROUTE_VERIFICATION}")

    return ZoneAccess(
        zone=zone,
        status=status,
        provenance=provenance,
        deciding_road_id=deciding.id if deciding else None,
        deciding_road_name=deciding.name if deciding else None,
        known_road_ids=[r.id for r in known],
        inferred_issue_ids=[r.id for r in inferred],
        verification_needed=verification,
    )
