"""Resource allocation: three alternative plans built only from resources that actually exist.

## Inputs
Prioritised incidents (priority + requirement estimate + zone access + already-assigned resources),
the resource registry, and optional travel estimates per (resource, incident), e.g. from OSRM.
Only resources with status `available` are considered.

## Need per incident
remaining(type) = required units (engines/resources.py) - units of that type already assigned.

## Feasibility
- Known closure of every known route (`ZoneAccess.ground_access_possible` is False): resources without
  the `water_rescue` capability (ambulances, vehicles, ...) cannot be sent by road. The need stays
  unmet with an explicit reason; no alternative route is invented.
- Boats are allowed but ALWAYS carry the assumption BOAT_ASSUMPTION: nominal capacity alone never
  establishes that water conditions are safe.
- Partially blocked access: allowed, with an assumption that travel may take longer.
- Unknown/unconfirmed access, unknown people count or a verification_required level: the allocation is
  flagged `verification_required`.
- Unknown location: nothing is allocated; it becomes a verification task.
- Pending potential duplicates (`duplicate_of` set) get no separate allocation, so the same people
  are not counted twice. The plan states this as an assumption.

## Candidate ranking (per needed type)
1. capability match (water_rescue preferred when boats are needed), 2. travel time (provided estimate,
or haversine x 1.3 at 30 km/h labelled "estimated"), 3. larger capacity, 4. resource ID (determinism).
A `used` set guarantees no resource appears twice within a plan.

## Variants
- A Priority-first: incidents by score (desc); each gets its full remaining need before the next.
- B Coverage: first one unit of each needed type to every critical/high incident (score order),
  then top up all incidents in score order.
- C Verified-access only: allocate (priority-first) only where access is decided by KNOWN road data
  and the level is not verification_required. Other incidents become verification tasks.

## Shortages (identical in every plan: they describe the situation, not the plan)
For each resource type: required = sum of remaining need over allocatable incidents,
available = count of `available` resources of that type, deficit = required - available (listed if > 0).
The note recommends escalation and never names an invented resource.

## Plan score (used only to pick the recommended plan)
plan_score = 100 x sum(priority_score_i x coverage_i) / sum(priority_score_i), where
coverage_i = allocated units / remaining required units (incidents with no remaining need are skipped).
Highest score is recommended; ties go to the earlier variant (A, then B, then C).
"""

from typing import Literal

from app.engines.access import ZoneAccess
from app.engines.geo import estimate_travel
from app.engines.models import IncidentFacts, ResourceFacts
from app.engines.priority import level_for
from app.engines.resources import RequirementEstimate
from app.models.schemas import (
    AllocatedResource,
    Contract,
    PlanAllocation,
    PriorityAssessment,
    ResourceShortage,
    ResourceType,
    UnmetRequirement,
)

BOAT_ASSUMPTION = "Water and route conditions permit boat deployment: verification required"
AVAILABLE_ONLY = "Only resources with status 'available' were considered (reserved, deployed, returning and unavailable excluded)."
ESTIMATED_TRAVEL = "Travel times marked 'estimated' are straight-line distance x 1.3 at 30 km/h, not routed."

TYPE_LABEL: dict[ResourceType, str] = {
    "rescue_boat": "rescue boats",
    "rescue_team": "rescue teams",
    "ambulance": "ambulances",
    "emergency_vehicle": "emergency vehicles",
    "medical_team": "medical teams",
}
ESCALATION: dict[ResourceType, str] = {
    "rescue_boat": "Escalate to district or state emergency operations for additional water-rescue capacity.",
    "rescue_team": "Request additional rescue teams through district or state emergency operations.",
    "ambulance": "Request mutual-aid ambulances from neighbouring districts or the health department.",
    "emergency_vehicle": "Request additional transport through district emergency operations.",
    "medical_team": "Request additional medical teams from the health department.",
}

Variant = Literal["A", "B", "C"]
VARIANTS: dict[Variant, tuple[str, str]] = {
    "A": ("Plan A: Priority-first",
          "Fill the highest-priority incident's full requirement before moving to the next."),
    "B": ("Plan B: Coverage",
          "Give every critical/high incident a minimum response (one unit of each needed type) first, "
          "then top up in priority order."),
    "C": ("Plan C: Verified access only",
          "Allocate only where access is confirmed by known road data and the report needs no verification; "
          "everything else becomes a verification task."),
}


class TravelInfo(Contract):
    distance_km: float
    minutes: float
    source: Literal["osrm", "estimated"]


class AllocationInput(Contract):
    incident: IncidentFacts
    priority: PriorityAssessment
    requirements: RequirementEstimate
    access: ZoneAccess
    assigned: list[ResourceFacts] = []

    def remaining(self, rtype: ResourceType) -> int:
        have = sum(r.type == rtype for r in self.assigned)
        return max(0, self.requirements.units(rtype) - have)

    @property
    def needed_types(self) -> list[ResourceType]:
        return [i.resource_type for i in self.requirements.items if self.remaining(i.resource_type) > 0]


class PlanDraft(Contract):
    """A plan before persistence: the agents add id, run_id, explanation and critic findings."""

    variant: Variant
    title: str
    strategy: str
    allocations: list[PlanAllocation]
    shortages: list[ResourceShortage]
    assumptions: list[str]
    plan_score: float
    is_recommended: bool = False


class _Builder:
    """Mutable state while building ONE plan."""

    def __init__(self, resources: list[ResourceFacts], travel: dict[tuple[str, str], TravelInfo]):
        self.pool = [r for r in resources if r.status == "available"]
        self.travel = travel
        self.used: set[str] = set()
        self.picked: dict[str, list[AllocatedResource]] = {}
        self.notes: dict[str, list[str]] = {}
        self.used_estimates = False

    def note(self, incident_id: str, text: str) -> None:
        notes = self.notes.setdefault(incident_id, [])
        if text not in notes:
            notes.append(text)

    def count(self, incident_id: str, rtype: ResourceType) -> int:
        return sum(a.resource_type == rtype for a in self.picked.get(incident_id, []))

    def _travel(self, r: ResourceFacts, inc: IncidentFacts) -> TravelInfo:
        info = self.travel.get((r.id, inc.id))
        if info is None:
            km, minutes = estimate_travel(r.location, inc.location)  # location checked by caller
            info = TravelInfo(distance_km=round(km, 2), minutes=round(minutes, 1), source="estimated")
            self.used_estimates = True
        return info

    def allocate(self, inp: AllocationInput, rtype: ResourceType, units: int) -> None:
        if units <= 0:
            return
        inc, access = inp.incident, inp.access
        candidates = [r for r in self.pool if r.type == rtype and r.id not in self.used]
        if not access.ground_access_possible:
            if any("water_rescue" not in r.capabilities for r in candidates):
                self.note(inc.id, (
                    f"{TYPE_LABEL[rtype].capitalize()} cannot be sent by road: known closure of "
                    f"{access.deciding_road_name} ({access.deciding_road_id}). Alternative access: route verification required."
                ))
            candidates = [r for r in candidates if "water_rescue" in r.capabilities]

        wants_water = inp.requirements.units("rescue_boat") > 0
        ranked = sorted(
            ((r, self._travel(r, inc)) for r in candidates),
            key=lambda rt: (
                0 if (not wants_water or "water_rescue" in rt[0].capabilities) else 1,
                rt[1].minutes, -rt[0].capacity, rt[0].id,
            ),
        )
        for r, t in ranked[:units]:
            self.used.add(r.id)
            self.picked.setdefault(inc.id, []).append(AllocatedResource(
                resource_id=r.id, resource_type=r.type, travel_minutes=t.minutes, travel_source=t.source,
                reason=f"{r.name} ({r.base_name}), capacity {r.capacity}, ~{t.minutes:.0f} min away ({t.source})",
            ))
            if r.type == "rescue_boat":
                self.note(inc.id, BOAT_ASSUMPTION)

    def fill(self, inp: AllocationInput, cap_per_type: int | None = None) -> None:
        for rtype in inp.needed_types:
            missing = inp.remaining(rtype) - self.count(inp.incident.id, rtype)
            self.allocate(inp, rtype, missing if cap_per_type is None else min(cap_per_type, missing))

    def result(self, inp: AllocationInput, deferred_reason: str | None = None) -> PlanAllocation:
        inc, access = inp.incident, inp.access
        assumptions = list(self.notes.get(inc.id, []))
        verification = False
        if deferred_reason:
            assumptions.insert(0, deferred_reason)
            verification = True
        if access.status == "partially_blocked":
            assumptions.append(f"Access to {inc.zone} partially blocked ({access.deciding_road_name}): travel may take longer.")
        if access.status == "unknown" or access.provenance != "known":
            verification = True
            assumptions.append(f"Access to {inc.zone} is not confirmed by known road data: route verification required.")
        if inp.requirements.verification_needed or inp.priority.level == "verification_required":
            verification = True
            assumptions.extend(inp.requirements.verification_needed)
        assumptions.extend(f"{n} (outside this registry)" for n in inp.requirements.external_needs)

        unmet = []
        for item in inp.requirements.items:
            required = inp.remaining(item.resource_type)
            got = self.count(inc.id, item.resource_type)
            if required > got:
                unmet.append(UnmetRequirement(resource_type=item.resource_type, required=required,
                                              allocated=got, shortfall=required - got))
        return PlanAllocation(
            incident_id=inc.id, priority=inp.priority.level, resources=self.picked.get(inc.id, []),
            unmet=unmet, verification_required=verification, assumptions=list(dict.fromkeys(assumptions)),
        )


def _by_priority(inputs: list[AllocationInput]) -> list[AllocationInput]:
    return sorted(inputs, key=lambda i: (-i.priority.score, i.incident.id))


def compute_shortages(inputs: list[AllocationInput], resources: list[ResourceFacts]) -> list[ResourceShortage]:
    shortages = []
    for rtype in TYPE_LABEL:
        needing = [i for i in inputs if i.remaining(rtype) > 0]
        required = sum(i.remaining(rtype) for i in needing)
        available = sum(r.type == rtype and r.status == "available" for r in resources)
        if required > available:
            deficit = required - available
            shortages.append(ResourceShortage(
                resource_type=rtype, required=required, available=available, deficit=deficit,
                incident_ids=[i.incident.id for i in _by_priority(needing)],
                note=f"Deficit of {deficit} {TYPE_LABEL[rtype]}. {ESCALATION[rtype]}",
            ))
    return shortages


def plan_score(allocations: list[PlanAllocation], inputs: list[AllocationInput]) -> float:
    weights = covered = 0.0
    by_id = {a.incident_id: a for a in allocations}
    for inp in inputs:
        required = sum(inp.remaining(t) for t in inp.needed_types)
        if required == 0:
            continue
        got = len(by_id[inp.incident.id].resources) if inp.incident.id in by_id else 0
        weights += inp.priority.score
        covered += inp.priority.score * min(1.0, got / required)
    return round(100 * covered / weights, 1) if weights else 100.0


def build_plan(
    variant: Variant,
    inputs: list[AllocationInput],
    resources: list[ResourceFacts],
    travel: dict[tuple[str, str], TravelInfo] | None = None,
) -> PlanDraft:
    b = _Builder(resources, travel or {})
    plan_notes = [AVAILABLE_ONLY]

    duplicates = [i for i in inputs if i.incident.duplicate_of]
    for d in duplicates:
        plan_notes.append(
            f"{d.incident.id} not allocated separately: linked as potential duplicate of "
            f"{d.incident.duplicate_of} (pending human review)."
        )
    candidates = _by_priority([i for i in inputs if not i.incident.duplicate_of])
    deferred: dict[str, str] = {
        i.incident.id: "Location unknown: no allocation until the location is confirmed."
        for i in candidates if i.incident.location is None
    }
    if variant == "C":
        for i in candidates:
            if i.incident.id in deferred:
                continue
            if i.access.status == "unknown" or i.access.provenance != "known":
                deferred[i.incident.id] = "Verified-access plan: deferred until access is confirmed by known road data."
            elif i.priority.level == "verification_required":
                deferred[i.incident.id] = "Verified-access plan: deferred until the report is verified."
    active = [i for i in candidates if i.incident.id not in deferred]

    if variant == "B":
        for inp in active:
            if level_for(inp.priority.score) in ("critical", "high"):
                b.fill(inp, cap_per_type=1)
    for inp in active:
        b.fill(inp)

    allocations = [b.result(i, deferred.get(i.incident.id)) for i in candidates]
    if b.used_estimates:
        plan_notes.append(ESTIMATED_TRAVEL)
    title, strategy = VARIANTS[variant]
    return PlanDraft(
        variant=variant, title=title, strategy=strategy, allocations=allocations,
        shortages=compute_shortages(candidates, resources), assumptions=plan_notes,
        plan_score=plan_score(allocations, candidates),
    )


def generate_plans(
    inputs: list[AllocationInput],
    resources: list[ResourceFacts],
    travel: dict[tuple[str, str], TravelInfo] | None = None,
) -> list[PlanDraft]:
    """Plans A, B and C, with exactly one marked recommended."""
    plans = [build_plan(v, inputs, resources, travel) for v in ("A", "B", "C")]
    best = max(plans, key=lambda p: p.plan_score)  # max() keeps the first on ties -> A before B before C
    best.is_recommended = True
    return plans
