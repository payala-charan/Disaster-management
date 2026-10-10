"""Simulation controls for the live demo and the TC-01...TC-08 scenarios.

Scenario steps (data/scenarios/*.json) are applied through the SAME services the real API uses
(intake, road, weather, shelter, resource, incident updates), so the demo exercises the real code paths.
A test-case setup always starts from a clean seed, applies its steps without intermediate runs, and
then performs ONE agent run, so the screen shows a single coherent recommendation.
"""

from datetime import timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import models as m
from app.db.seed import load_scenario, load_seed, write_seed, write_setup
from app.db.session import Base
from app.models import schemas as s
from app.models.seed_data import (
    IncidentUpdateStep,
    ReportStep,
    ResourceStep,
    RoadStep,
    Scenario,
    ShelterStep,
    WeatherStep,
)
from app.services import actions as action_svc
from app.services import operations as ops
from app.services.context import Services
from app.services.errors import NotFound

ACTOR = "simulation"

# Which scenario and which steps (by index) set up each test case.
TEST_CASES: dict[str, dict[str, Any]] = {
    "TC-01": {"scenario": "source_reports", "steps": [0], "title": "New flood incident reported"},
    "TC-02": {"scenario": "source_reports", "steps": [0, 1], "title": "Same incident from two sources"},
    "TC-03": {"scenario": None, "steps": [], "title": "Critical incident with available resources"},
    "TC-04": {"scenario": "shortage", "steps": None, "title": "Required resources exceed availability"},
    "TC-05": {"scenario": "road_changes", "steps": [0], "title": "Assigned road becomes blocked",
              "approve_before": "INC-002"},
    "TC-06": {"scenario": "shelter_pressure", "steps": [0, 1], "title": "Shelter approaches capacity"},
    "TC-07": {"scenario": "weather_timeline", "steps": [0], "title": "Weather forecast worsens"},
    "TC-08": {"scenario": "double_allocation", "steps": None, "title": "Same resource considered for two incidents"},
}


def reset(svc: Services) -> None:
    """Wipe all tables and load the seed (same as `python -m app.db.seed --reset`, without dropping tables)."""
    with svc.session_factory() as db:
        tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        db.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        db.commit()
        write_seed(db, load_seed(), now=svc.now())
    svc.deps.weather._cache.clear()  # forget live readings from before the reset
    svc.bus.publish("situation_changed", {"reason": "simulation reset"})


def apply_step(svc: Services, db: Session, step) -> str:
    """Apply one scenario step through the normal services. Returns a short description."""
    if isinstance(step, ReportStep):
        rid = ops.submit_report(svc, db, s.IntakeReportIn(source=step.source, text=step.text, payload=step.payload))
        return f"report {rid} ({step.source})"
    if isinstance(step, RoadStep):
        ops.update_road(svc, db, step.road_id, s.RoadPatch(status=step.status, provenance=step.provenance,
                                                           note=step.road_note), ACTOR)
        return f"road {step.road_id} -> {step.status}"
    if isinstance(step, WeatherStep):
        ops.inject_weather(svc, db, s.WeatherInject(**step.model_dump(exclude={"kind", "at_minute", "note", "forecast_hourly"}),
                                                    forecast_hourly=[h.model_dump() for h in step.forecast_hourly]))
        return f"weather {step.zone} -> {step.condition}"
    if isinstance(step, ShelterStep):
        ops.update_shelter(svc, db, step.shelter_id, s.ShelterPatch(current_occupancy=step.current_occupancy,
                                                                    status=step.status), ACTOR)
        return f"shelter {step.shelter_id} -> {step.current_occupancy}"
    if isinstance(step, ResourceStep):
        eta = svc.now() + timedelta(minutes=step.expected_available_in_min) if step.expected_available_in_min else None
        ops.change_resource_status(svc, db, step.resource_id, s.ResourceStatusPatch(
            status=step.status, reason=step.reason, expected_available_at=eta), ACTOR)
        return f"resource {step.resource_id} -> {step.status}"
    if isinstance(step, IncidentUpdateStep):
        _update_incident_fields(svc, db, step)
        return f"incident {step.incident_id} updated: {sorted(step.changes)}"
    raise ValueError(f"unknown step {step!r}")


def _update_incident_fields(svc: Services, db: Session, step: IncidentUpdateStep) -> None:
    """Scenario updates may also carry new threat indicators reported from the field."""
    inc = db.get(m.Incident, step.incident_id)
    simple = {k: v for k, v in step.changes.items() if k in s.IncidentPatch.model_fields}
    if "threat_indicators" in step.changes:
        inc.threat_indicators = list(step.changes["threat_indicators"])
        inc.field_provenance = {**inc.field_provenance, "threat_indicators": step.provenance}
        db.flush()
    if simple:
        ops.patch_incident(svc, db, step.incident_id, s.IncidentPatch(**simple), ACTOR)
    else:
        db.commit()


def setup_test_case(svc: Services, tc_id: str) -> dict[str, Any]:
    if tc_id not in TEST_CASES:
        raise NotFound(f"Unknown test case {tc_id}; known: {', '.join(TEST_CASES)}")
    spec = TEST_CASES[tc_id]
    reset(svc)
    applied: list[str] = []
    if spec.get("approve_before"):
        # TC-05 needs an APPROVED action first: run once, then approve the recommendation for that incident.
        svc.run_now("manual")
        with svc.session_factory() as db:
            action = db.scalars(select(m.Action).where(m.Action.status == "pending",
                                                       m.Action.incident_id == spec["approve_before"])).first()
            if action:
                action_svc.approve(svc, db, action.id, ACTOR, f"Approved to set up {tc_id}")
                applied.append(f"approved {action.id}")
    if spec["scenario"]:
        sc: Scenario = load_scenario(spec["scenario"])
        indices = spec["steps"] if spec["steps"] is not None else range(len(sc.steps))
        with svc.session_factory() as db:
            write_setup(db, sc.setup, now=svc.now())
            db.commit()
            for i in indices:
                applied.append(apply_step(svc, db, sc.steps[i]))
    trigger = "new_report" if spec["scenario"] == "source_reports" else "manual"
    run = svc.run_now(trigger)
    return {"test_case": tc_id, "title": spec["title"], "applied": applied,
            "run": {k: run[k] for k in s.RunResult.model_fields}}
