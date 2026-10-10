import itertools

import pytest

from app.engines.access import assess_zone_access
from app.engines.models import IncidentFacts, RoadFacts
from app.engines.resources import InvalidTransition, allowed_transitions, estimate_requirements, validate_transition
from app.models.schemas import GeoPoint

from .conftest import NOW

STATUSES = ["available", "reserved", "deployed", "returning", "unavailable"]
VALID = {
    ("available", "reserved"), ("reserved", "deployed"), ("reserved", "available"),
    ("deployed", "returning"), ("returning", "available"), ("unavailable", "available"),
    *((s, "unavailable") for s in STATUSES if s != "unavailable"),
}


@pytest.mark.parametrize("old, new", list(itertools.product(STATUSES, STATUSES)))
def test_full_transition_matrix(old, new):
    if (old, new) in VALID:
        validate_transition(old, new)
    else:
        with pytest.raises(InvalidTransition):
            validate_transition(old, new)


def test_allowed_transitions_for_ui():
    assert allowed_transitions("available") == ["reserved", "unavailable"]
    assert allowed_transitions("unavailable") == ["available"]


# ---------------------------------------------------------------- requirements

def units(est):
    return {i.resource_type: i.units for i in est.items}


def test_seed_requirements(scenario):
    req = {i: scenario.allocation_input(i).requirements for i in ("INC-001", "INC-002", "INC-003")}
    assert units(req["INC-001"]) == {"rescue_boat": 5, "rescue_team": 2}                 # 40 people, partial access
    assert units(req["INC-002"]) == {"rescue_boat": 2, "rescue_team": 1}                 # 12 stranded
    assert units(req["INC-003"]) == {"rescue_boat": 10, "rescue_team": 3, "ambulance": 4}  # 75, closed, medical
    boat = next(i for i in req["INC-003"].items if i.resource_type == "rescue_boat")
    assert boat.formula == "ceil(75 people / 8 per boat) = 10"


def incident(**kw) -> IncidentFacts:
    base = dict(id="INC-T", type="flood", title="t", description="d", zone="Z", location=GeoPoint(lat=13, lng=80),
                reported_at=NOW, people_affected=20, confidence="high", verification_status="verified")
    return IncidentFacts(**(base | kw))


def access(status="open"):
    return assess_zone_access("Z", [RoadFacts(id="R", name="R", serves_zone="Z", status=status, provenance="known")])


def test_flood_evacuation_with_open_road_uses_vehicles():
    est = estimate_requirements(incident(assistance_requested=["evacuation"], people_affected=25), access("open"))
    assert units(est) == {"emergency_vehicle": 3, "rescue_team": 1}


def test_unknown_people_gives_minimum_and_verification_never_a_guess():
    est = estimate_requirements(
        incident(people_affected=None, assistance_requested=["water_rescue"], threat_indicators=["stranded"]), access())
    assert units(est) == {"rescue_boat": 1, "rescue_team": 1}
    assert all(i.verification_required for i in est.items)
    assert "Confirm number of people affected" in est.verification_needed


def test_trapped_needs_search_and_rescue_teams():
    est = estimate_requirements(incident(type="landslide", people_affected=15, threat_indicators=["trapped"]), access())
    assert units(est) == {"rescue_team": 1}


def test_injury_adds_ambulances():
    est = estimate_requirements(incident(people_affected=100, threat_indicators=["injured"]), access())
    assert units(est) == {"ambulance": 5}


def test_fire_is_an_external_need_not_an_invented_resource():
    est = estimate_requirements(
        incident(type="urban_emergency", people_affected=None, threat_indicators=["trapped", "fire"]), access())
    assert set(units(est)) == {"rescue_team"}
    assert any("fire services" in n for n in est.external_needs)


def test_unclear_needs_are_flagged():
    est = estimate_requirements(incident(assistance_requested=[], threat_indicators=[]), access())
    assert est.items == [] and est.verification_needed
