"""End-to-end runs of the LangGraph workflow on the seed scenario (LLM mocked, PostGIS test database)."""

from collections import Counter
from datetime import datetime, timedelta, timezone

import openai
import pytest
from sqlalchemy import func, select

from app.db import models as m
from app.db.repository import next_id
from app.db.seed import load_scenario, write_setup
from app.engines.allocation import BOAT_ASSUMPTION
from app.graph.workflow import mermaid, run_assessment

pytestmark = pytest.mark.db

AGENTS = ["incident_intake", "weather_intelligence", "geospatial_accessibility", "resource_management",
          "situation_assessment", "resource_allocation", "reviewer_critic", "emergency_coordination"]


def count(db, model, *where):
    return db.scalar(select(func.count()).select_from(model).where(*where))


def test_seed_run_end_to_end(deps, session_factory):
    result = run_assessment("manual", deps)
    assert result["status"] == "completed" and result["errors"] == []
    assert Counter(result["agents"]) == Counter(AGENTS)  # each agent once: critic passed first time

    with session_factory() as db:
        plans = db.scalars(select(m.Plan).order_by(m.Plan.id)).all()
        assert [p.variant for p in plans] == ["A", "B", "C"]
        recommended = [p for p in plans if p.is_recommended]
        assert len(recommended) == 1 and recommended[0].variant == "B" and result["recommended_plan_id"] == recommended[0].id
        assert not any(f["severity"] == "blocking" for p in plans for f in p.critic_findings)

        # Priorities computed by the engine (seed had none).
        latest = {r.incident_id: (r.score, r.level) for r in db.scalars(select(m.PriorityAssessmentRow))}
        assert latest["INC-003"] == (89, "critical") and latest["INC-002"] == (58, "high")

        # Human in the loop: actions are pending and NOTHING was reserved.
        actions = db.scalars(select(m.Action).where(m.Action.status == "pending")).all()
        assert {a.id for a in actions} == set(result["pending_actions"]) and len(actions) == 3
        assert count(db, m.ResourceAssignment) == 2  # only the seeded ACT-001 assignments
        assert {r.status for r in db.scalars(select(m.Resource).where(m.Resource.id.in_(["BOAT-002", "BOAT-005"])))} == {"available"}

        # No double allocation across pending actions, and none of them uses an already-assigned resource.
        proposed = [rid for a in actions for rid in a.resource_ids]
        assert len(proposed) == len(set(proposed)) and not {"BOAT-001", "TEAM-001"} & set(proposed)

        # The pending duplicate is not planned separately.
        assert "INC-004" not in {a.incident_id for a in actions}

        # Explainability: rationale, assumptions, inputs with provenance, deadline, audit event.
        inc3 = next(a for a in actions if a.incident_id == "INC-003")
        assert inc3.priority == "critical" and inc3.due_by - inc3.created_at == timedelta(minutes=30)
        assert BOAT_ASSUMPTION in inc3.assumptions
        assert {"Access route", "People affected"} <= {i["label"] for i in inc3.inputs_used}
        assert db.scalar(select(m.ActionEvent.event).where(m.ActionEvent.action_id == inc3.id)) == "created"

        # Alerts and the agent trace are persisted.
        kinds = Counter(a.kind for a in db.scalars(select(m.Alert)))
        assert kinds["critical_incident"] == 1
        steps = db.scalars(select(m.AgentStep).where(m.AgentStep.run_id == result["run_id"])).all()
        assert Counter(s.agent for s in steps) == Counter(AGENTS)
        assert all(s.tool_calls and s.error is None for s in steps)
        assert db.get(m.AgentRun, result["run_id"]).status == "completed"


def test_second_run_supersedes_pending_and_does_not_repeat_alerts(deps, session_factory):
    first = run_assessment("manual", deps)
    second = run_assessment("manual", deps)
    with session_factory() as db:
        for aid in first["pending_actions"]:
            action = db.get(m.Action, aid)
            assert action.status == "superseded"
            assert "superseded" in {e.event for e in db.scalars(select(m.ActionEvent).where(m.ActionEvent.action_id == aid))}
        assert db.get(m.Action, "ACT-001").status == "in_progress"  # human decision untouched
        assert count(db, m.Plan, m.Plan.status == "proposed") == 3
        assert count(db, m.Alert, m.Alert.kind == "critical_incident") == 1   # not re-raised
        assert count(db, m.Alert, m.Alert.kind == "weather_worsening") == 3   # only on the first run
        assert count(db, m.PriorityAssessmentRow) == 4                         # unchanged scores: no new rows
    assert len(second["pending_actions"]) == 3


def add_raw_reports(session_factory, scenario: str) -> list[str]:
    sc = load_scenario(scenario)
    now = datetime.now(timezone.utc)
    with session_factory() as db:
        write_setup(db, sc.setup)
        ids = []
        for step in sc.steps:
            if step.kind != "report":
                continue
            raw = m.RawReport(id=next_id(db, m.RawReport, "RAW"), source=step.source, raw_text=step.text,
                              raw_payload=step.payload, received_at=now + timedelta(minutes=step.at_minute))
            db.add(raw)
            ids.append(raw.id)
        db.commit()
    return ids


def test_intake_of_five_source_types(deps, session_factory, fake_openai):
    add_raw_reports(session_factory, "source_reports")
    result = run_assessment("new_report", deps)
    assert result["status"] == "completed"
    assert "ExtractedIncident" in fake_openai.calls  # LLM path used for free text

    with session_factory() as db:
        new = {i.source: i for i in db.scalars(select(m.Incident).where(m.Incident.id > "INC-004"))}
        assert set(new) == {"emergency_call", "citizen_report", "field_team", "iot_sensor"}  # no incident from the advisory

        call = new["emergency_call"]
        assert (call.type, call.zone, call.people_affected) == ("flood", "Zone D", 25)
        assert call.field_provenance["people_affected"] == "reported"
        assert call.field_provenance["location"] == "inferred"  # zone centre approximation

        citizen = new["citizen_report"]
        assert citizen.people_affected is None and citizen.confidence == "low"
        link = db.scalars(select(m.DuplicateLink).where(m.DuplicateLink.incident_id == citizen.id)).one()
        assert (link.candidate_id, link.classification, link.status) == (call.id, "potential_duplicate", "pending")

        field = new["field_team"]
        assert field.people_affected is None and field.verification_status == "verified"
        assert field.field_provenance["location"] == "reported"  # explicit coordinates

        sensor = new["iot_sensor"]
        assert sensor.confidence == "low" and sensor.people_affected is None
        sensor_level = db.scalars(select(m.PriorityAssessmentRow.level)
                                  .where(m.PriorityAssessmentRow.incident_id == sensor.id)).one()
        assert sensor_level == "verification_required"

        assert db.scalars(select(m.Alert).where(m.Alert.message.contains("Red alert"))).first() is not None
        observed = db.scalars(select(m.ConditionChange).where(m.ConditionChange.entity_id == "RD-B")).all()
        assert [(c.new_value, c.provenance) for c in observed] == [("partially_blocked", "reported")]
        assert db.get(m.RoadSegment, "RD-B").status == "open"  # registry keeps known facts only

        pending = db.scalars(select(m.Action).where(m.Action.status == "pending")).all()
        assert citizen.id not in {a.incident_id for a in pending}


def test_critic_routes_back_when_a_resource_changes_during_planning(deps, session_factory, monkeypatch):
    """A field update makes BOAT-002 unavailable while plans are being built. The critic re-reads the
    database, blocks the plans that use it, and allocation re-plans without it."""
    import app.agents.planning as planning

    real = planning.generate_plans
    calls = {"n": 0}

    def generate_then_break_boat(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            with session_factory() as db:
                db.get(m.Resource, "BOAT-002").status = "unavailable"
                db.commit()
        return real(*args, **kwargs)

    monkeypatch.setattr(planning, "generate_plans", generate_then_break_boat)
    result = run_assessment("manual", deps)

    assert Counter(result["agents"])["resource_allocation"] == 2
    assert Counter(result["agents"])["reviewer_critic"] == 2
    with session_factory() as db:
        pending = db.scalars(select(m.Action).where(m.Action.status == "pending")).all()
        assert pending and all("BOAT-002" not in a.resource_ids for a in pending)
        critic_steps = db.scalars(select(m.AgentStep).where(m.AgentStep.agent == "reviewer_critic")
                                  .order_by(m.AgentStep.id)).all()
        assert "blocking" in critic_steps[0].output_summary and "routing back" in critic_steps[0].output_summary


def test_llm_outage_degrades_gracefully(deps, session_factory, fake_openai):
    fake_openai.fail = openai.APITimeoutError(request=None)
    add_raw_reports(session_factory, "source_reports")
    result = run_assessment("new_report", deps)
    assert result["status"] == "completed" and result["pending_actions"]
    with session_factory() as db:
        intake = db.scalars(select(m.AgentStep).where(m.AgentStep.agent == "incident_intake")).one()
        assert intake.llm_used is False
        assert any("fallback (APITimeoutError)" in t["result_summary"] for t in intake.tool_calls)
        call = db.scalars(select(m.Incident).where(m.Incident.source == "emergency_call",
                                                    m.Incident.id > "INC-004")).one()
        assert call.people_affected == 25  # regex fallback still extracted it


def test_failed_agent_is_recorded_and_run_continues(deps, session_factory, monkeypatch):
    import app.agents.weather_intel as weather_intel

    monkeypatch.setattr(weather_intel.repo, "previous_run_started",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("weather store down")))
    result = run_assessment("manual", deps)
    assert result["status"] == "completed"
    assert any("weather_intelligence: RuntimeError" in e for e in result["errors"])
    with session_factory() as db:
        step = db.scalars(select(m.AgentStep).where(m.AgentStep.agent == "weather_intelligence")).one()
        assert step.error and step.output_summary.startswith("FAILED")
        # Without weather, severity flags it instead of guessing, and the critic reports the gap.
        inc3 = db.scalars(select(m.PriorityAssessmentRow).where(m.PriorityAssessmentRow.incident_id == "INC-003")).one()
        assert "Weather data unavailable" in inc3.verification_needed
        plan = db.scalars(select(m.Plan).where(m.Plan.is_recommended)).one()
        assert any(f["check"] == "agent_contradiction" for f in plan.critic_findings)


def test_mermaid_diagram_shows_agents_fan_out_and_loop():
    diagram = mermaid()
    for agent in AGENTS:
        assert agent in diagram
    assert "incident_intake --> weather_intelligence" in diagram
    assert "reviewer_critic -.-> resource_allocation" in diagram  # conditional loop-back


def test_run_after_database_reset_starts_from_clean_state(deps, session_factory, test_engine):
    """Regression: run IDs restart after a reset; a reused checkpoint thread would merge old state."""
    from sqlalchemy import text

    from app.db.seed import load_seed, write_seed
    from app.db.session import Base

    first = run_assessment("manual", deps)
    with test_engine.begin() as conn:
        conn.execute(text("TRUNCATE " + ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
                          + " RESTART IDENTITY CASCADE"))
    with session_factory() as db:
        write_seed(db, load_seed())
    second = run_assessment("manual", deps)
    assert first["run_id"] == second["run_id"] == "RUN-001" and first["thread_id"] != second["thread_id"]
    assert len(second["agents"]) == len(AGENTS)
    with session_factory() as db:
        assert count(db, m.Alert, m.Alert.kind == "critical_incident") == 1


def test_related_link_does_not_corroborate(session_factory):
    """Only potential_duplicate links count as 'linked to a verified incident'."""
    from app.db.repository import incident_facts

    with session_factory() as db:
        db.add(m.DuplicateLink(id="DUP-099", incident_id="INC-003", candidate_id="INC-002", score=0.55,
                               classification="related", breakdown={}, status="pending"))
        db.flush()
        facts = incident_facts(db)
        assert not facts["INC-003"].linked_to_verified and facts["INC-003"].duplicate_of is None
        assert facts["INC-004"].linked_to_verified and facts["INC-004"].duplicate_of == "INC-002"
