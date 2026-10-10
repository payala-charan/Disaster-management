import pytest

from app.engines.models import WeatherFacts
from app.engines.replanning import (
    ActionRef, ConditionChangeEvent, IncidentRef, analyze_change, road_worsened, weather_worsened,
)

INCIDENTS = [
    IncidentRef(id="INC-001", zone="Zone A", evacuation_requested=True),
    IncidentRef(id="INC-002", zone="Zone B"),
    IncidentRef(id="INC-003", zone="Zone C", evacuation_requested=True),
    IncidentRef(id="INC-009", zone="Zone B", status="resolved"),
]
ACTIONS = [
    ActionRef(id="ACT-001", incident_id="INC-001", resource_ids=["BOAT-001", "TEAM-001"], status="in_progress"),
    ActionRef(id="ACT-002", incident_id="INC-002", resource_ids=["BOAT-002", "BOAT-003"], status="pending"),
    ActionRef(id="ACT-003", incident_id="INC-002", resource_ids=["VEH-001"], status="approved"),
    ActionRef(id="ACT-004", incident_id="INC-002", resource_ids=["VEH-002"], status="completed"),
]


def test_road_closure_tc05():
    change = ConditionChangeEvent(kind="road", entity_id="RD-B", zone="Zone B", old_value="open", new_value="closed")
    impact = analyze_change(change, INCIDENTS, ACTIONS)
    assert impact.worsened and impact.trigger_run
    assert impact.reassess_incident_ids == ["INC-002"]      # resolved INC-009 ignored
    assert impact.supersede_action_ids == ["ACT-002"]       # pending -> replaced by a new recommendation
    assert impact.review_action_ids == ["ACT-003"]          # approved -> human decides; completed untouched


def test_road_improvement_supersedes_pending_but_needs_no_review():
    change = ConditionChangeEvent(kind="road", entity_id="RD-B", zone="Zone B", old_value="closed", new_value="open")
    impact = analyze_change(change, INCIDENTS, ACTIONS)
    assert not impact.worsened
    assert impact.supersede_action_ids == ["ACT-002"] and impact.review_action_ids == []


@pytest.mark.parametrize("old, new, worse", [
    ("open", "partially_blocked", True), ("partially_blocked", "closed", True), ("open", "unknown", True),
    ("closed", "open", False), ("closed", "closed", False),
])
def test_road_worsened(old, new, worse):
    assert road_worsened(old, new) is worse


def test_resource_unavailable_hits_actions_using_it():
    change = ConditionChangeEvent(kind="resource", entity_id="BOAT-001", new_value="unavailable")
    impact = analyze_change(change, INCIDENTS, ACTIONS)
    assert impact.reassess_incident_ids == ["INC-001"]
    assert impact.review_action_ids == ["ACT-001"] and impact.supersede_action_ids == []


def test_resource_available_reassesses_pending_recommendations():
    change = ConditionChangeEvent(kind="resource", entity_id="BOAT-004", new_value="available")
    impact = analyze_change(change, INCIDENTS, ACTIONS)
    assert impact.reassess_incident_ids == ["INC-002"] and impact.review_action_ids == []


def test_shelter_full_reassesses_evacuations_only():
    full = analyze_change(ConditionChangeEvent(kind="shelter", entity_id="SHL-A", new_value="full"), INCIDENTS, ACTIONS)
    assert full.reassess_incident_ids == ["INC-001", "INC-003"]
    occ = analyze_change(ConditionChangeEvent(kind="shelter", entity_id="SHL-A", new_value="open"), INCIDENTS, ACTIONS)
    assert not occ.trigger_run


def test_weather_change_reassesses_zone_incidents():
    change = ConditionChangeEvent(kind="weather", entity_id="Zone C", zone="Zone C", worsened=True)
    impact = analyze_change(change, INCIDENTS, ACTIONS)
    assert impact.reassess_incident_ids == ["INC-003"] and impact.worsened


def w(rain=10.0, forecast=10.0, wind=30.0, severe=False):
    return WeatherFacts(zone="Z", condition="x", rainfall_mm_per_hr=rain, wind_kph=wind,
                        severe_warning=severe, forecast_max_mm_per_hr=forecast, source="simulation")


@pytest.mark.parametrize("old, new, worse", [
    (w(), w(severe=True), True),
    (w(forecast=24), w(forecast=38), True),   # weather_timeline.json step 1 (+14 mm/h)
    (w(), w(rain=14.9, forecast=14.9), False),  # +4.9 mm/h: below the 5 mm/h rule
    (w(wind=32), w(wind=55), True),           # crosses 50 km/h
    (w(wind=55), w(wind=60), False),          # already above
    (w(severe=True, forecast=24), w(rain=6, forecast=6), False),  # easing
    (None, w(severe=True), True),
])
def test_weather_worsened(old, new, worse):
    assert weather_worsened(old, new) is worse
