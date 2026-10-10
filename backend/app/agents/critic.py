"""Agent 8: Reviewer / Critic. Deterministic checks on every plan before anything reaches a human.

| check | severity | rule |
|---|---|---|
| double_allocation | blocking | a resource appears more than once in a plan |
| resource_validity | blocking | resource unknown, not 'available' in the DATABASE NOW, or already actively assigned |
| capability | blocking | allocated type is not one the incident requires |
| route_claim | blocking | a resource without water capability sent to a zone whose known routes are all closed |
| boat_assumption | blocking | boats allocated without the boat-verification assumption |
| shortage_math | blocking | deficit != required - available, or a shortage with no deficit |
| priority_consistency | blocking | recomputing the priority from the run's inputs gives a different score/level |
| unsupported_claim | blocking | an explanation names an ID or number not present in the plan (grounding check) |
| stale_access | warning | the road registry changed during the run (a new run will follow) |
| agent_contradiction | warning | an incident has no weather or access result from the other agents |
| shelter_capacity | warning/info | people needing evacuation exceed free shelter space / shelters at threshold |
| verification | info | allocations flagged verification_required |

Blocking findings on resources (validity) feed `excluded_resource_ids`, and the graph routes back to
allocation (at most MAX_ITERATIONS times). Coordination never turns a plan with blocking findings
into actions.
"""

from collections import Counter
from typing import Any

from app.agents.base import Deps, StepRecorder, agent
from app.agents.planning import allocation_inputs
from app.db import repository as repo
from app.engines.access import ZoneAccess, assess_zone_access
from app.engines.allocation import BOAT_ASSUMPTION
from app.engines.models import IncidentFacts, ResourceFacts, WeatherFacts
from app.engines.priority import assess_priority
from app.engines.shelters import available as shelter_available
from app.integrations.grounding import check_grounding
from app.models.schemas import CriticFinding, PriorityAssessment, Shelter

MAX_ITERATIONS = 2


def finding(check: str, severity: str, message: str, related: list[str]) -> CriticFinding:
    return CriticFinding(check=check, severity=severity, message=message, related_ids=related)


def review_plan(plan: dict[str, Any], *, live_resources: dict[str, ResourceFacts], active: dict[str, str],
                requirements: dict[str, dict], zone_access: dict[str, ZoneAccess],
                incidents: dict[str, IncidentFacts]) -> list[CriticFinding]:
    """Checks that concern a single plan. Pure: all inputs are passed in."""
    out: list[CriticFinding] = []
    used = [r["resource_id"] for a in plan["allocations"] for r in a["resources"]]
    for rid, n in Counter(used).items():
        if n > 1:
            out.append(finding("double_allocation", "blocking", f"{rid} allocated {n} times in plan {plan['variant']}", [rid]))

    for a in plan["allocations"]:
        inc = incidents.get(a["incident_id"])
        required_types = {i["resource_type"] for i in requirements.get(a["incident_id"], {}).get("items", [])}
        access = zone_access.get(inc.zone) if inc else None
        for r in a["resources"]:
            rid = r["resource_id"]
            live = live_resources.get(rid)
            if live is None:
                out.append(finding("resource_validity", "blocking", f"{rid} does not exist in the registry", [rid]))
            elif live.status != "available" or rid in active:
                out.append(finding("resource_validity", "blocking",
                                   f"{rid} is now '{live.status}'" + (f" and assigned to {active[rid]}" if rid in active else ""),
                                   [rid, a["incident_id"]]))
            if r["resource_type"] not in required_types:
                out.append(finding("capability", "blocking",
                                   f"{rid} ({r['resource_type']}) is not a required type for {a['incident_id']}",
                                   [rid, a["incident_id"]]))
            if access and not access.ground_access_possible and live and "water_rescue" not in live.capabilities:
                out.append(finding("route_claim", "blocking",
                                   f"{rid} cannot reach {inc.zone} by road: known closure of {access.deciding_road_id}",
                                   [rid, a["incident_id"], access.deciding_road_id or ""]))
        if any(r["resource_type"] == "rescue_boat" for r in a["resources"]) and BOAT_ASSUMPTION not in a["assumptions"]:
            out.append(finding("boat_assumption", "blocking",
                               f"Boats for {a['incident_id']} lack the water-conditions verification assumption",
                               [a["incident_id"]]))
        if a["verification_required"]:
            out.append(finding("verification", "info", f"{a['incident_id']}: allocation requires verification",
                               [a["incident_id"]]))

    for s in plan["shortages"]:
        if s["deficit"] != s["required"] - s["available"] or s["deficit"] <= 0:
            out.append(finding("shortage_math", "blocking",
                               f"Shortage for {s['resource_type']} is inconsistent: {s['required']} required, "
                               f"{s['available']} available, deficit {s['deficit']}", []))

    problems = check_grounding(plan.get("explanation", ""), {k: v for k, v in plan.items() if k != "explanation"})
    if problems:
        out.append(finding("unsupported_claim", "blocking", f"Explanation not supported by plan data: {problems}", []))
    return out


def review_situation(state: dict[str, Any], shelters: list[Shelter], fresh_access: dict[str, ZoneAccess],
                     threshold: float) -> list[CriticFinding]:
    """Checks that apply to the run as a whole (same for every plan)."""
    out: list[CriticFinding] = []
    incidents = {k: IncidentFacts(**v) for k, v in state.get("incidents", {}).items()}
    weather = {z: WeatherFacts(**w) for z, w in state.get("weather", {}).items()}
    access = {z: ZoneAccess(**a) for z, a in state.get("zone_access", {}).items()}

    for inp in allocation_inputs(state):
        inc = inp.incident
        recomputed = assess_priority(inc, inp.access, weather.get(inc.zone), inp.requirements, inp.assigned,
                                     inp.priority.assessed_at)
        if (recomputed.score, recomputed.level) != (inp.priority.score, inp.priority.level):
            out.append(finding("priority_consistency", "blocking",
                               f"{inc.id}: stored priority {inp.priority.score} {inp.priority.level} but inputs give "
                               f"{recomputed.score} {recomputed.level}", [inc.id]))
    for iid, inc in incidents.items():
        if inc.zone not in access or iid not in state.get("access_analysis", {}):
            out.append(finding("agent_contradiction", "warning", f"{iid}: no access analysis for {inc.zone}", [iid]))
        if inc.zone not in weather:
            out.append(finding("agent_contradiction", "warning", f"{iid}: no weather data for {inc.zone}", [iid]))

    for zone, planned in access.items():
        now = fresh_access.get(zone)
        if now and (now.status, now.provenance) != (planned.status, planned.provenance):
            out.append(finding("stale_access", "warning",
                               f"Access to {zone} changed during this run ({planned.status} -> {now.status}): reassessment will follow",
                               [now.deciding_road_id or zone]))

    evacuees = sum(i.people_affected or 0 for i in incidents.values()
                   if "evacuation" in i.assistance_requested and not i.duplicate_of)
    free = sum(shelter_available(s) for s in shelters if s.status == "open")
    if evacuees > free:
        out.append(finding("shelter_capacity", "warning",
                           f"{evacuees} people may need shelter but only {free} places are free", [s.id for s in shelters]))
    for s in shelters:
        if s.capacity and s.current_occupancy / s.capacity >= threshold:
            out.append(finding("shelter_capacity", "info",
                               f"{s.id} at {s.current_occupancy}/{s.capacity} (threshold {threshold:.0%})", [s.id]))
    return out


@agent("reviewer_critic")
def reviewer_critic(state: dict[str, Any], deps: Deps, rec: StepRecorder, db) -> dict[str, Any]:
    iteration = state.get("critic_iterations", 0) + 1
    live = {r.id: r for r in rec.tool("resource_registry.list", {"fresh": True}, lambda: repo.resource_facts(db),
                                      lambda r: f"{len(r)} resources re-read")}
    active = repo.active_assignments(db)
    roads = rec.tool("road_registry.list", {"fresh": True}, lambda: repo.road_facts(db), lambda r: f"{len(r)} roads")
    shelters = repo.shelters(db)
    incidents = {k: IncidentFacts(**v) for k, v in state.get("incidents", {}).items()}
    zone_access = {z: ZoneAccess(**a) for z, a in state.get("zone_access", {}).items()}
    fresh_access = {z: assess_zone_access(z, roads) for z in zone_access}
    rec.input_summary = f"Reviewing {len(state.get('plans', []))} plan(s), iteration {iteration}"

    shared = review_situation(state, shelters, fresh_access, deps.settings.shelter_alert_threshold)
    findings: dict[str, list[dict[str, Any]]] = {}
    excluded = set(state.get("excluded_resource_ids", []))
    for plan in state.get("plans", []):
        own = review_plan(plan, live_resources=live, active=active, requirements=state.get("requirements", {}),
                          zone_access=zone_access, incidents=incidents)
        excluded |= {f.related_ids[0] for f in own if f.check == "resource_validity"}
        findings[plan["variant"]] = [f.model_dump(mode="json") for f in [*own, *shared]]

    blocking = any(f["severity"] == "blocking" for fs in findings.values() for f in fs)
    counts = Counter(f["severity"] for fs in findings.values() for f in fs)
    rec.output_summary = (f"Iteration {iteration}: {counts.get('blocking', 0)} blocking, {counts.get('warning', 0)} "
                          f"warning, {counts.get('info', 0)} info finding(s) across plans"
                          + ("; routing back to allocation" if blocking and iteration < MAX_ITERATIONS + 1 else ""))
    return {"critic_findings": findings, "critic_iterations": iteration, "blocking": blocking,
            "excluded_resource_ids": sorted(excluded)}


def route_after_critic(state: dict[str, Any]) -> str:
    """Back to allocation while blocking issues remain and re-planning attempts are left."""
    if state.get("blocking") and state.get("critic_iterations", 0) <= MAX_ITERATIONS:
        return "resource_allocation"
    return "emergency_coordination"
