import pytest

from app.engines.access import assess_zone_access
from app.engines.models import IncidentFacts, ResourceFacts, RoadFacts, WeatherFacts
from app.engines.priority import assess_priority, level_for
from app.engines.resources import RequirementEstimate, estimate_requirements
from app.models.schemas import GeoPoint

from .conftest import NOW


def points(assessment, name: str) -> int:
    return next(f.points for f in assessment.factors if f.name == name)


# ---------------------------------------------------------------- seed scenario expectations

def test_inc003_is_critical_89(scenario):
    p = scenario.allocation_input("INC-003").priority
    assert (p.score, p.level) == (89, "critical")
    assert [f.points for f in p.factors] == [18, 20, 15, 13, 8, 15]
    assert any("Riverside Link Road" in v and "Route verification required" in v for v in p.verification_needed)


def test_inc002_is_high(scenario):
    p = scenario.allocation_input("INC-002").priority
    assert (p.score, p.level) == (58, "high")


def test_inc001_is_high_because_assigned_resources_fall_short(scenario):
    """Documented deviation from the UI mock (53/Moderate): 1 of 5 boats and 1 of 2 teams assigned."""
    p = scenario.allocation_input("INC-001").priority
    assert (p.score, p.level) == (61, "high")
    gap = next(f for f in p.factors if f.name == "Response gap")
    assert gap.points == 8 and "rescue_boat 1/5" in gap.value


def test_inc004_unknown_people_needs_verification_but_is_linked_to_verified(scenario):
    p = scenario.allocation_input("INC-004").priority
    assert points(p, "People affected") == 0
    assert "Confirm number of people affected" in p.verification_needed
    assert p.level == "moderate"  # low-confidence but linked to verified INC-002 -> no override


def test_factor_provenance_distinguishes_known_and_reported(scenario):
    p = scenario.allocation_input("INC-003").priority
    prov = {f.name: f.provenance for f in p.factors}
    assert prov["Access route"] == "known"           # road registry
    assert prov["People affected"] == "reported"     # from the report
    assert prov["Infrastructure impact"] == "known"  # government alert, per field_provenance


def test_reasons_are_templated_from_factors(scenario):
    p = scenario.allocation_input("INC-003").priority
    assert "Access route: Main Road to Zone C closed (15/15)" in p.reasons
    for reason in p.reasons:  # every reason quotes a factor; nothing is free-form
        assert any(reason.startswith(f.name) for f in p.factors)


# ---------------------------------------------------------------- rule tiers and boundaries

def make_incident(**overrides) -> IncidentFacts:
    base = dict(
        id="INC-T", type="flood", title="t", description="d", zone="Z", location=GeoPoint(lat=13, lng=80),
        reported_at=NOW, people_affected=10, confidence="high", verification_status="verified",
    )
    return IncidentFacts(**(base | overrides))


def assess(incident=None, roads=None, weather=None, assigned=None, requirements=None):
    incident = incident or make_incident()
    access = assess_zone_access(incident.zone, roads if roads is not None else [
        RoadFacts(id="R1", name="Road 1", serves_zone="Z", status="open", provenance="known")])
    req = requirements or estimate_requirements(incident, access)
    return assess_priority(incident, access, weather, req, assigned or [], NOW)


@pytest.mark.parametrize("indicators, expected", [
    (["trapped"], 22), (["stranded"], 22), (["injured"], 22), (["water_rescue"], 22),
    (["isolated"], 18), (["cut_off"], 18), (["evacuation_needed"], 12),
    (["property_damage_only"], 5), (["evacuation_needed", "trapped"], 22), ([], 0),
])
def test_threat_tiers(indicators, expected):
    p = assess(make_incident(threat_indicators=indicators))
    assert points(p, "Threat to life") == expected
    assert ("Confirm threat to life" in p.verification_needed) == (expected == 0)


@pytest.mark.parametrize("people, expected", [
    (None, 0), (0, 0), (1, 5), (9, 5), (10, 8), (24, 8), (25, 14), (49, 14), (50, 20), (500, 20),
])
def test_people_tiers(people, expected):
    assert points(assess(make_incident(people_affected=people)), "People affected") == expected


@pytest.mark.parametrize("status, provenance, expected", [
    ("open", "known", 0), ("partially_blocked", "known", 8), ("closed", "known", 15),
    ("unknown", "known", 10), ("closed", "inferred", 10), ("open", "reported", 0),
])
def test_access_tiers(status, provenance, expected):
    p = assess(roads=[RoadFacts(id="R1", name="Road 1", serves_zone="Z", status=status, provenance=provenance)])
    assert points(p, "Access route") == expected
    if expected == 10 or provenance != "known":
        assert any("Route verification required" in v for v in p.verification_needed)


def test_no_roads_at_all_is_unknown_access():
    p = assess(roads=[])
    assert points(p, "Access route") == 10


def weather(rain=0.0, forecast=0.0, wind=10.0, severe=False) -> WeatherFacts:
    return WeatherFacts(zone="Z", condition="x", rainfall_mm_per_hr=rain, wind_kph=wind,
                        severe_warning=severe, forecast_max_mm_per_hr=forecast, source="simulation")


@pytest.mark.parametrize("w, expected", [
    (weather(rain=12, forecast=10, severe=True), 13),
    (weather(rain=12, forecast=9.9, severe=True), 10),  # warning but forecast below heavy -> heavy now
    (weather(rain=10), 10), (weather(forecast=10), 10),
    (weather(rain=9.9), 5), (weather(rain=2.5), 5), (weather(rain=2.4), 0),
    (weather(rain=0, wind=50), 2), (weather(rain=12, forecast=12, severe=True, wind=55), 15),
])
def test_weather_tiers(w, expected):
    assert points(assess(weather=w), "Weather / escalation") == expected


def test_missing_weather_is_flagged_not_guessed():
    p = assess(weather=None)
    assert points(p, "Weather / escalation") == 0
    assert "Weather data unavailable" in p.verification_needed


@pytest.mark.parametrize("issues, expected", [
    (["main_road_closed"], 8), (["road_partially_flooded"], 6), (["power_outage"], 6),
    (["bridge_damaged"], 4), ([], 0), (["bridge_damaged", "main_road_closed"], 8),
])
def test_infrastructure_tiers(issues, expected):
    assert points(assess(make_incident(infrastructure_issues=issues)), "Infrastructure impact") == expected


def boat(i: int) -> ResourceFacts:
    return ResourceFacts(id=f"BOAT-{i}", type="rescue_boat", name="b", base_name="x",
                         location=GeoPoint(lat=13, lng=80), status="deployed", capacity=8)


def test_response_gap_tiers():
    req = RequirementEstimate(incident_id="INC-T", external_needs=[], verification_needed=[], items=[
        {"resource_type": "rescue_boat", "units": 2, "formula": "f", "verification_required": False}])
    assert points(assess(requirements=req, assigned=[]), "Response gap") == 15
    assert points(assess(requirements=req, assigned=[boat(1)]), "Response gap") == 8
    assert points(assess(requirements=req, assigned=[boat(1), boat(2)]), "Response gap") == 0


@pytest.mark.parametrize("score, level", [
    (100, "critical"), (75, "critical"), (74, "high"), (55, "high"), (54, "moderate"),
    (35, "moderate"), (34, "low"), (0, "low"),
])
def test_level_boundaries(score, level):
    assert level_for(score) == level


def test_unknown_location_overrides_level_but_keeps_score():
    p = assess(make_incident(location=None, threat_indicators=["trapped"], people_affected=80))
    assert p.level == "verification_required" and p.score > 0
    assert p.verification_needed[0].startswith("Location unknown")


def test_low_confidence_unverified_unlinked_is_verification_required():
    inc = make_incident(confidence="low", verification_status="unverified", linked_to_verified=False)
    assert assess(inc).level == "verification_required"
    assert assess(inc.model_copy(update={"linked_to_verified": True})).level != "verification_required"
    assert assess(inc.model_copy(update={"confidence": "medium"})).level != "verification_required"
