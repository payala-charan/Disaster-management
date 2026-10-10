import random
from collections import Counter

import pytest

from app.engines.access import assess_zone_access
from app.engines.allocation import (
    BOAT_ASSUMPTION, AllocationInput, TravelInfo, build_plan, compute_shortages, generate_plans,
)
from app.engines.models import IncidentFacts, ResourceFacts, RoadFacts
from app.engines.priority import assess_priority
from app.engines.resources import estimate_requirements
from app.models.schemas import GeoPoint

from .conftest import NOW


def ids(allocation):
    return [r.resource_id for r in allocation.resources]


def alloc(plan, incident_id):
    return next(a for a in plan.allocations if a.incident_id == incident_id)


# ---------------------------------------------------------------- seed scenario

@pytest.fixture
def seed_plans(scenario):
    return {p.variant: p for p in generate_plans(scenario.allocation_inputs(), scenario.resources)}


def test_three_variants_exactly_one_recommended(seed_plans):
    assert list(seed_plans) == ["A", "B", "C"]
    assert sum(p.is_recommended for p in seed_plans.values()) == 1
    assert seed_plans["B"].is_recommended  # coverage beats priority-first when boats are this scarce


def test_priority_first_fills_inc003_first(seed_plans):
    a = alloc(seed_plans["A"], "INC-003")
    assert Counter(r.resource_type for r in a.resources) == {"rescue_boat": 4, "rescue_team": 3}
    assert ids(alloc(seed_plans["A"], "INC-002")) == []


def test_coverage_gives_every_high_incident_a_minimum(seed_plans):
    for inc in ("INC-003", "INC-001", "INC-002"):
        types = {r.resource_type for r in alloc(seed_plans["B"], inc).resources}
        assert {"rescue_boat", "rescue_team"} <= types


def test_ambulances_not_sent_down_a_known_closed_road(seed_plans):
    a = alloc(seed_plans["A"], "INC-003")
    assert not any(r.resource_type == "ambulance" for r in a.resources)
    unmet = {u.resource_type: u.shortfall for u in a.unmet}
    assert unmet["ambulance"] == 4
    assert any("cannot be sent by road" in s and "RD-C" in s for s in a.assumptions)


def test_boats_always_carry_the_verification_assumption(seed_plans):
    for plan in seed_plans.values():
        for a in plan.allocations:
            if any(r.resource_type == "rescue_boat" for r in a.resources):
                assert BOAT_ASSUMPTION in a.assumptions


def test_duplicate_is_not_allocated_separately(seed_plans):
    for plan in seed_plans.values():
        assert "INC-004" not in {a.incident_id for a in plan.allocations}
        assert any("INC-004" in s and "INC-002" in s for s in plan.assumptions)


def test_seed_shortage_has_exact_deficit_and_no_invented_resources(seed_plans, scenario):
    s = {x.resource_type: x for x in seed_plans["A"].shortages}
    boats = s["rescue_boat"]
    # need: INC-003 10 + INC-001 (5 - 1 assigned) 4 + INC-002 2 = 16; available BOAT-002..005 = 4
    assert (boats.required, boats.available, boats.deficit) == (16, 4, 12)
    assert boats.incident_ids == ["INC-003", "INC-001", "INC-002"]
    assert "Escalate" in boats.note
    assert "ambulance" not in s  # 4 needed, 5 available: an access problem, not a shortage


def test_only_available_registry_resources_are_used_and_never_twice(seed_plans, scenario):
    available = {r.id for r in scenario.resources if r.status == "available"}
    for plan in seed_plans.values():
        used = [r for a in plan.allocations for r in ids(a)]
        assert len(used) == len(set(used)), f"double allocation in plan {plan.variant}"
        assert set(used) <= available
        assert not {"BOAT-001", "TEAM-001", "AMB-006", "VEH-008"} & set(used)


def test_plans_are_deterministic(scenario):
    one = generate_plans(scenario.allocation_inputs(), scenario.resources)
    two = generate_plans(scenario.allocation_inputs(), scenario.resources)
    assert [p.model_dump() for p in one] == [p.model_dump() for p in two]


# ---------------------------------------------------------------- synthetic cases

ZONE_CENTER = GeoPoint(lat=13.0, lng=80.2)


def make_input(id, people, *, zone="Z", status="open", provenance="known", indicators=("stranded",),
               assistance=("water_rescue",), location=ZONE_CENTER, confidence="high", verified="verified"):
    inc = IncidentFacts(id=id, type="flood", title=id, description=id, zone=zone, location=location,
                        reported_at=NOW, people_affected=people, assistance_requested=list(assistance),
                        threat_indicators=list(indicators), confidence=confidence, verification_status=verified)
    roads = [RoadFacts(id=f"R-{zone}", name=f"Road {zone}", serves_zone=zone, status=status, provenance=provenance)]
    acc = assess_zone_access(zone, roads)
    req = estimate_requirements(inc, acc)
    return AllocationInput(incident=inc, priority=assess_priority(inc, acc, None, req, [], NOW),
                           requirements=req, access=acc)


def fleet(prefix, rtype, n, caps, lat=13.05, status="available", capacity=8):
    return [ResourceFacts(id=f"{prefix}-{i:03d}", type=rtype, name=f"{prefix} {i}", base_name="Base",
                          location=GeoPoint(lat=lat + i * 0.001, lng=80.25), status=status,
                          capacity=capacity, capabilities=caps) for i in range(1, n + 1)]


def test_eight_needed_five_available_gives_deficit_of_three():
    inp = make_input("INC-T", 64)  # ceil(64 / 8) = 8 boats
    boats = fleet("BOAT", "rescue_boat", 5, ["water_rescue"])
    teams = fleet("TEAM", "rescue_team", 3, ["water_rescue"])
    plan = build_plan("A", [inp], boats + teams)
    boat_short = next(s for s in plan.shortages if s.resource_type == "rescue_boat")
    assert (boat_short.required, boat_short.available, boat_short.deficit) == (8, 5, 3)
    a = plan.allocations[0]
    assert sum(r.resource_type == "rescue_boat" for r in a.resources) == 5
    assert next(u for u in a.unmet if u.resource_type == "rescue_boat").shortfall == 3


def test_same_resource_considered_for_two_incidents_goes_to_one():
    """TC-08 at engine level: one boat, two incidents needing boats."""
    inputs = [make_input("INC-A", 8, zone="A"), make_input("INC-B", 8, zone="B")]
    boat = fleet("BOAT", "rescue_boat", 1, ["water_rescue"])
    for plan in generate_plans(inputs, boat):
        holders = [a.incident_id for a in plan.allocations if "BOAT-001" in ids(a)]
        assert len(holders) == 1


def test_travel_time_ranks_candidates_and_provided_estimates_win():
    inp = make_input("INC-T", 8)
    near, far = fleet("BOAT", "rescue_boat", 2, ["water_rescue"])
    travel = {(near.id, "INC-T"): TravelInfo(distance_km=30, minutes=60, source="osrm"),
              (far.id, "INC-T"): TravelInfo(distance_km=5, minutes=10, source="osrm")}
    picked = build_plan("A", [inp], [near, far], travel).allocations[0].resources[0]
    assert (picked.resource_id, picked.travel_source) == (far.id, "osrm")


def test_unknown_access_is_flagged_and_excluded_from_plan_c():
    inp = make_input("INC-T", 8, status="closed", provenance="inferred")
    boats = fleet("BOAT", "rescue_boat", 2, ["water_rescue"])
    a_plan = build_plan("A", [inp], boats)
    assert a_plan.allocations[0].verification_required and a_plan.allocations[0].resources
    c_plan = build_plan("C", [inp], boats)
    assert c_plan.allocations[0].resources == []
    assert "Verified-access plan" in c_plan.allocations[0].assumptions[0]


def test_unknown_location_gets_no_resources():
    inp = make_input("INC-T", 8, location=None)
    plan = build_plan("A", [inp], fleet("BOAT", "rescue_boat", 2, ["water_rescue"]))
    assert plan.allocations[0].resources == [] and plan.allocations[0].verification_required


def test_estimated_travel_is_labelled():
    plan = build_plan("A", [make_input("INC-T", 8)], fleet("BOAT", "rescue_boat", 1, ["water_rescue"]))
    assert plan.allocations[0].resources[0].travel_source == "estimated"
    assert any("estimated" in s for s in plan.assumptions)


def test_shortages_are_independent_of_variant(scenario):
    inputs = scenario.allocation_inputs()
    candidates = [i for i in inputs if not i.incident.duplicate_of]
    expected = compute_shortages(candidates, scenario.resources)
    for plan in generate_plans(inputs, scenario.resources):
        assert plan.shortages == expected


@pytest.mark.parametrize("seed", range(25))
def test_random_scenarios_never_double_allocate(seed):
    rng = random.Random(seed)
    zones = ["A", "B", "C"]
    inputs = [
        make_input(f"INC-{i}", rng.choice([None, 3, 12, 40, 90]), zone=rng.choice(zones),
                   status=rng.choice(["open", "partially_blocked", "closed"]),
                   provenance=rng.choice(["known", "known", "inferred"]),
                   indicators=rng.choice([("stranded",), ("trapped",), ("injured",), ()]),
                   assistance=rng.choice([("water_rescue",), ("evacuation", "medical_check"), ("evacuation",)]))
        for i in range(rng.randint(1, 6))
    ]
    resources = (fleet("BOAT", "rescue_boat", rng.randint(0, 5), ["water_rescue"])
                 + fleet("TEAM", "rescue_team", rng.randint(0, 4), ["water_rescue"])
                 + fleet("AMB", "ambulance", rng.randint(0, 4), ["medical_transport"], capacity=2)
                 + fleet("VEH", "emergency_vehicle", rng.randint(0, 4), ["road_transport"], capacity=10)
                 + fleet("BUSY", "rescue_boat", 2, ["water_rescue"], status="deployed"))
    registry = {r.id: r for r in resources}
    for plan in generate_plans(inputs, resources):
        used = [rid for a in plan.allocations for rid in ids(a)]
        assert len(used) == len(set(used))
        assert all(registry[rid].status == "available" for rid in used)
        for s in plan.shortages:
            assert s.deficit == s.required - s.available > 0
