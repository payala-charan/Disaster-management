from datetime import timedelta

import pytest

from app.engines.access import assess_zone_access
from app.engines.duplicates import classify, find_candidates, score_pair
from app.engines.geo import estimate_travel, haversine_m
from app.engines.models import RoadFacts
from app.engines.shelters import (
    can_accommodate, crossed_threshold, nearest_with_capacity, shelter_state,
)
from app.models.schemas import GeoPoint, Shelter

# ---------------------------------------------------------------- geo


def test_haversine_one_degree_latitude():
    assert haversine_m(GeoPoint(lat=0, lng=0), GeoPoint(lat=1, lng=0)) == pytest.approx(111_195, rel=1e-3)


def test_fallback_travel_formula():
    a, b = GeoPoint(lat=0, lng=0), GeoPoint(lat=0.1, lng=0)  # ~11.12 km straight line
    km, minutes = estimate_travel(a, b)
    assert km == pytest.approx(11.12 * 1.3, rel=1e-3)
    assert minutes == pytest.approx(km / 30 * 60)


# ---------------------------------------------------------------- access


def road(id, status, provenance, zone="Z"):
    return RoadFacts(id=id, name=f"Road {id}", serves_zone=zone, status=status, provenance=provenance)


def test_best_known_road_wins():
    a = assess_zone_access("Z", [road("R1", "closed", "known"), road("R2", "open", "known")])
    assert (a.status, a.deciding_road_id, a.ground_access_possible) == ("open", "R2", True)


def test_inferred_road_never_decides_access(scenario):
    zone_c = scenario.access("Zone C")
    assert (zone_c.status, zone_c.provenance, zone_c.deciding_road_id) == ("closed", "known", "RD-C")
    assert zone_c.inferred_issue_ids == ["RD-C2"]
    assert not zone_c.ground_access_possible


def test_only_inferred_road_means_unknown():
    a = assess_zone_access("Z", [road("R1", "closed", "inferred")])
    assert (a.status, a.provenance) == ("unknown", "inferred")
    assert a.ground_access_possible  # an AI guess must not block ground resources like a known closure


def test_reported_road_used_only_without_known_and_flagged():
    a = assess_zone_access("Z", [road("R1", "partially_blocked", "reported")])
    assert (a.status, a.provenance) == ("partially_blocked", "reported")
    assert any("Route verification required" in v for v in a.verification_needed)


# ---------------------------------------------------------------- duplicates


def test_seed_citizen_report_is_potential_duplicate_of_field_report(scenario):
    inc = scenario.incidents
    found = find_candidates(inc["INC-004"], list(inc.values()))
    assert found[0].candidate_id == "INC-002"
    assert found[0].classification == "potential_duplicate"
    b = found[0].breakdown
    assert b.distance_m < 100 and b.time_diff_min == 5 and b.type_match == 1
    assert all(c.candidate_id != "INC-004" for c in found)  # never matched with itself


def test_score_is_the_documented_weighted_sum(scenario):
    inc = scenario.incidents
    score, b = score_pair(inc["INC-004"], inc["INC-002"])
    expected = 0.35 * b.distance_score + 0.25 * b.time_score + 0.15 * b.type_match + 0.25 * b.text_similarity
    assert score == pytest.approx(expected, abs=2e-3)


def test_far_apart_and_hours_apart_are_not_linked(scenario):
    inc = scenario.incidents
    assert find_candidates(inc["INC-003"], [inc["INC-002"]]) == []


def test_distance_and_time_scores_floor_at_zero(scenario):
    a = scenario.incidents["INC-002"]
    far = a.model_copy(update={"id": "X", "location": GeoPoint(lat=14, lng=81),
                               "reported_at": a.reported_at + timedelta(hours=5)})
    _, b = score_pair(a, far)
    assert b.distance_score == 0 and b.time_score == 0


def test_unknown_location_gives_zero_distance_score(scenario):
    a = scenario.incidents["INC-002"]
    _, b = score_pair(a, a.model_copy(update={"id": "X", "location": None}))
    assert b.distance_m is None and b.distance_score == 0


@pytest.mark.parametrize("score, label", [(0.70, "potential_duplicate"), (0.69, "related"), (0.50, "related"), (0.49, None)])
def test_classification_thresholds(score, label):
    assert classify(score) == label


# ---------------------------------------------------------------- shelters


def shelter(cap=200, occ=160, status="open", id="S", lat=13.0):
    return Shelter(id=id, name=id, location=GeoPoint(lat=lat, lng=80), capacity=cap, current_occupancy=occ, status=status)


def test_shelter_state_math():
    st = shelter_state(shelter(200, 160))
    assert (st.available, st.utilization, st.approaching_threshold, st.accepting) == (40, 0.8, False, True)
    assert shelter_state(shelter(200, 170)).approaching_threshold  # exactly 0.85
    assert shelter_state(shelter(100, 120)).available == 0          # overflow floors at 0
    assert shelter_state(shelter(0, 0)).utilization == 0


def test_can_accommodate():
    assert can_accommodate(shelter(200, 160), 40)
    assert not can_accommodate(shelter(200, 160), 41)
    assert not can_accommodate(shelter(200, 0, status="closed"), 1)


def test_threshold_crossing_fires_once():
    # Shelter pressure scenario: 160 -> 168 (84%) -> 172 (86%) -> 200
    assert not crossed_threshold(160, 168, 200)
    assert crossed_threshold(168, 172, 200)
    assert not crossed_threshold(172, 200, 200)


def test_nearest_shelter_with_capacity_skips_full_ones():
    here = GeoPoint(lat=13.0, lng=80)
    near_full = shelter(100, 100, id="NEAR", lat=13.001)
    far_ok = shelter(100, 10, id="FAR", lat=13.05)
    found, metres = nearest_with_capacity(here, [near_full, far_ok], people=5)
    assert found.id == "FAR" and metres > 5_000
    assert nearest_with_capacity(here, [near_full], people=1) is None
