"""Reviewer/critic checks as pure functions, on deliberately broken plans."""

import copy

import pytest

from app.agents.coordination import choose_plan
from app.agents.critic import review_plan
from app.engines.allocation import BOAT_ASSUMPTION, generate_plans
from app.integrations.llm import template_explanation
from tests.engines.conftest import Scenario


@pytest.fixture
def ctx(bundle):
    s = Scenario(bundle)
    inputs = s.allocation_inputs()
    plans = []
    for p in generate_plans(inputs, s.resources):
        d = p.model_dump(mode="json")
        d["explanation"] = template_explanation(d, "plan_explanation")
        plans.append(d)
    return {
        "plans": {p["variant"]: p for p in plans},
        "kwargs": dict(
            live_resources={r.id: r for r in s.resources}, active={"BOAT-001": "INC-001", "TEAM-001": "INC-001"},
            requirements={i.incident.id: i.requirements.model_dump() for i in inputs},
            zone_access={z: s.access(z) for z in ("Zone A", "Zone B", "Zone C")}, incidents=s.incidents,
        ),
    }


def checks(plan, ctx) -> set[str]:
    return {f.check for f in review_plan(plan, **ctx["kwargs"]) if f.severity == "blocking"}


@pytest.fixture
def bundle():
    from app.db.seed import load_seed

    return load_seed()


def test_engine_plans_pass(ctx):
    for plan in ctx["plans"].values():
        assert checks(plan, ctx) == set()


def inc003(plan):
    return next(a for a in plan["allocations"] if a["incident_id"] == "INC-003")


def test_double_allocation(ctx):
    plan = copy.deepcopy(ctx["plans"]["A"])
    a = inc003(plan)
    a["resources"].append(dict(a["resources"][0]))
    assert "double_allocation" in checks(plan, ctx)


def test_resource_unavailable_or_assigned_or_invented(ctx):
    for rid in ("BOAT-001", "VEH-008", "HELI-001"):  # assigned, unavailable, never existed
        plan = copy.deepcopy(ctx["plans"]["A"])
        inc003(plan)["resources"][0]["resource_id"] = rid
        assert "resource_validity" in checks(plan, ctx), rid


def test_ground_vehicle_to_closed_zone_is_a_false_route_claim(ctx):
    plan = copy.deepcopy(ctx["plans"]["A"])
    inc003(plan)["resources"].append({"resource_id": "AMB-001", "resource_type": "ambulance", "reason": "x",
                                      "travel_minutes": 10, "travel_source": "estimated"})
    assert "route_claim" in checks(plan, ctx)


def test_type_not_required(ctx):
    plan = copy.deepcopy(ctx["plans"]["A"])
    inc003(plan)["resources"].append({"resource_id": "VEH-001", "resource_type": "emergency_vehicle", "reason": "x",
                                      "travel_minutes": 10, "travel_source": "estimated"})
    assert "capability" in checks(plan, ctx)


def test_missing_boat_assumption(ctx):
    plan = copy.deepcopy(ctx["plans"]["A"])
    inc003(plan)["assumptions"].remove(BOAT_ASSUMPTION)
    assert "boat_assumption" in checks(plan, ctx)


def test_shortage_math(ctx):
    plan = copy.deepcopy(ctx["plans"]["A"])
    plan["shortages"][0]["deficit"] += 1
    assert "shortage_math" in checks(plan, ctx)


def test_unsupported_claim_in_explanation(ctx):
    plan = copy.deepcopy(ctx["plans"]["A"])
    plan["explanation"] += " A helicopter HELI-001 can lift 40 people."
    assert "unsupported_claim" in checks(plan, ctx)


def test_coordination_never_recommends_a_blocked_plan(ctx):
    plans = list(ctx["plans"].values())
    findings = {"B": [{"severity": "blocking", "check": "x"}]}
    assert choose_plan(plans, findings)["variant"] == "A"   # best remaining (A ties C on score -> A)
    assert choose_plan(plans, {v: [{"severity": "blocking"}] for v in "ABC"}) is None
