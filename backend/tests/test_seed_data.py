"""Seed and scenario data: valid, internally consistent, and faithful to the frontend mock."""

import json

import pytest
from pydantic import ValidationError

from app.db.seed import SCENARIO_DIR, SEED_DIR, check_consistency, list_scenarios, load_scenario, load_seed
from app.models.seed_data import RelativeTime


@pytest.fixture(scope="module")
def bundle():
    return load_seed()


def test_seed_is_consistent(bundle):
    assert check_consistency(bundle) == []


def test_raw_incidents_have_no_precomputed_priority():
    raw = json.loads((SEED_DIR / "incidents.json").read_text())
    assert all("priority" not in i and "duplicate_of" not in i for i in raw)


def test_seed_matches_frontend_mock(bundle):
    by_type: dict[str, int] = {}
    for r in bundle.resources:
        by_type[r.type] = by_type.get(r.type, 0) + 1
    assert by_type == {"rescue_boat": 5, "rescue_team": 4, "ambulance": 6, "emergency_vehicle": 8}

    status = {r.id: r.status for r in bundle.resources}
    assert status["BOAT-001"] == status["TEAM-001"] == "deployed"
    assert status["AMB-006"] == "returning"
    assert status["VEH-008"] == "unavailable"
    assert [i.id for i in bundle.incidents] == ["INC-001", "INC-002", "INC-003", "INC-004"]


def test_unknown_people_count_stays_null(bundle):
    inc004 = next(i for i in bundle.incidents if i.id == "INC-004")
    assert inc004.people_affected is None
    assert "people_affected" not in inc004.field_provenance


def test_known_closure_and_inferred_problem_are_distinct(bundle):
    roads = {r.id: r for r in bundle.roads}
    assert (roads["RD-C"].status, roads["RD-C"].provenance) == ("closed", "known")
    assert (roads["RD-C2"].status, roads["RD-C2"].provenance) == ("unknown", "inferred")


def test_consistency_check_catches_double_allocation(bundle):
    broken = load_seed()
    second = broken.actions[0].model_copy(update={"id": "ACT-999"})
    broken.actions.append(second)
    problems = check_consistency(broken)
    assert any("double-allocated" in p for p in problems)


def test_consistency_check_catches_unexplained_deployment():
    broken = load_seed()
    boat = next(r for r in broken.resources if r.id == "BOAT-002")
    boat.status = "deployed"
    assert any("BOAT-002 is deployed" in p for p in check_consistency(broken))


@pytest.mark.parametrize(
    "value", [{}, {"minutes_ago": 5, "minutes_ahead": 5}],
)
def test_relative_time_needs_exactly_one_field(value):
    with pytest.raises(ValidationError):
        RelativeTime.model_validate(value)


# ---------------------------------------------------------------- scenarios

@pytest.mark.parametrize("name", list_scenarios())
def test_scenario_is_valid_and_references_exist(name, bundle):
    sc = load_scenario(name)
    assert sc.id == name and sc.steps and sc.expected

    zones = {z.name for z in bundle.gazetteer.zones}
    roads = {r.id for r in bundle.roads} | {r.id for r in sc.setup.roads}
    shelters = {s.id for s in bundle.shelters} | {s.id for s in sc.setup.shelters}
    resources = {r.id for r in bundle.resources} | {r.id for r in sc.setup.resources}
    incidents = {i.id for i in bundle.incidents}

    # Setup only ADDS registry entries; it must not silently redefine seeded ones.
    assert not ({r.id for r in sc.setup.roads} & {r.id for r in bundle.roads})
    assert not ({s.id for s in sc.setup.shelters} & {s.id for s in bundle.shelters})
    assert not ({r.id for r in sc.setup.resources} & {r.id for r in bundle.resources})
    assert all(r.serves_zone in zones for r in sc.setup.roads)

    for step in sc.steps:
        match step.kind:
            case "road":
                assert step.road_id in roads, step
            case "shelter":
                assert step.shelter_id in shelters, step
            case "resource":
                assert step.resource_id in resources, step
            case "incident_update":
                assert step.incident_id in incidents, step
            case "weather":
                assert step.zone in zones, step

    minutes = [s.at_minute for s in sc.steps]
    assert minutes == sorted(minutes), "steps must be in timeline order"


def test_every_test_case_has_data():
    covered = {"TC-03"}  # TC-03 runs on the base seed (INC-003 critical, boats available)
    for name in list_scenarios():
        covered |= set(load_scenario(name).test_cases)
    assert covered >= {f"TC-0{n}" for n in range(1, 9)}


def test_all_five_source_types_are_represented():
    sources = {s.source for s in load_scenario("source_reports").steps if s.kind == "report"}
    assert sources == {"emergency_call", "field_team", "iot_sensor", "government_alert", "citizen_report"}


def test_scenario_readme_lists_every_file():
    readme = (SCENARIO_DIR / "README.md").read_text()
    assert all(f"`{name}.json`" in readme for name in list_scenarios())
