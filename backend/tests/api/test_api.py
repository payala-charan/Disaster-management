"""HTTP API tests against the PostGIS test database."""

import threading
from datetime import timedelta

import pytest
from pypdf import PdfReader
from sqlalchemy import func, select

from app.db import models as m
from app.models import schemas as s
from app.services import actions as action_svc
from app.services.errors import Conflict
from app.services.overdue import check_overdue

from .conftest import pending_for

pytestmark = pytest.mark.db


# ---------------------------------------------------------------- read endpoints

def test_health_and_docs(client):
    health = client.get("/api/health").json()
    assert health["status"] == "ok" and health["postgis"].startswith("3.5") and health["llm"] == "fallback only"
    assert client.get("/docs").status_code == 200


def test_situation_matches_contract(client, seeded_run):
    snap = s.SituationSnapshot.model_validate(client.get("/api/situation").json())
    by_id = {i.id: i for i in snap.incidents}
    assert by_id["INC-003"].priority.score == 89
    assert by_id["INC-004"].duplicate_of == "INC-002" and "INC-004" in by_id["INC-002"].related_reports
    assert by_id["INC-001"].assigned_resource_ids == ["BOAT-001", "TEAM-001"]
    assert {sh.resource_type: sh.deficit for sh in snap.shortages} == {"rescue_boat": 12, "rescue_team": 2}
    assert next(r for r in snap.resources if r.id == "BOAT-001").assigned_incident_id == "INC-001"


def test_access_analysis_separates_known_and_inferred(client):
    a = s.AccessAnalysis.model_validate(client.get("/api/access/INC-003").json())
    assert a.access_status == "inaccessible_by_road"
    assert [r.road_id for r in a.known_routes] == ["RD-C"] and [r.road_id for r in a.inferred_issues] == ["RD-C2"]
    assert a.travel_estimates and all(t.source == "estimated" for t in a.travel_estimates)
    assert client.get("/api/access/INC-999").status_code == 404


def test_runs_and_plans(client, seeded_run):
    runs = client.get("/api/assessments").json()
    assert runs[0]["id"] == seeded_run["run_id"] and len(runs[0]["steps"]) == 8
    detail = s.AgentRun.model_validate(client.get(f"/api/assessments/{seeded_run['run_id']}").json())
    assert {st.agent for st in detail.steps} >= {"incident_intake", "reviewer_critic"}
    plans = [s.Plan.model_validate(p) for p in client.get("/api/plans/latest").json()]
    assert [p.variant for p in plans] == ["A", "B", "C"] and sum(p.is_recommended for p in plans) == 1


# ---------------------------------------------------------------- incidents, duplicates, intake

def test_manual_incident_registration_triggers_assessment(client):
    body = {"type": "flood", "title": "Families trapped in school", "description": "Water rising around the school, "
            "30 people trapped on the first floor in Zone D", "zone": "Zone D", "location": {"lat": 13.066, "lng": 80.236},
            "reported_at": "2026-10-08T10:00:00Z", "source": "field_team", "people_affected": 30,
            "assistance_requested": ["water_rescue"]}
    created = client.post("/api/incidents", json=body, headers={"X-User": "Asha"})
    assert created.status_code == 201
    inc = created.json()
    assert inc["id"] == "INC-005" and inc["priority"] is None  # ID assigned by the system; assessment follows
    after = client.get("/api/incidents/INC-005").json()            # background run has finished
    assert after["priority"]["level"] in ("critical", "high", "moderate", "verification_required")
    assert client.get("/api/incidents/INC-404").status_code == 404


def test_patch_people_count_is_recorded_and_reassessed(client, seeded_run, session_factory):
    r = client.patch("/api/incidents/INC-003", json={"people_affected": 120})
    assert r.status_code == 200 and r.json()["people_affected"] == 120
    with session_factory() as db:
        change = db.scalars(select(m.ConditionChange).where(m.ConditionChange.field == "people_affected")
                            .order_by(m.ConditionChange.changed_at.desc())).first()
        assert (change.old_value, change.new_value, change.provenance) == ("75", "120", "reported")
    unknown = client.patch("/api/incidents/INC-003", json={"clear_people_affected": True}).json()
    assert unknown["people_affected"] is None


def test_duplicate_review(client, seeded_run):
    links = client.get("/api/incidents/INC-004/duplicates").json()
    assert links[0]["candidate_id"] == "INC-002" and links[0]["status"] == "pending"
    rejected = client.post(f"/api/duplicates/{links[0]['id']}/reject", headers={"X-User": "Ravi"}).json()
    assert rejected["status"] == "rejected" and rejected["reviewed_by"] == "Ravi"
    assert client.post(f"/api/duplicates/{links[0]['id']}/confirm").status_code == 409
    # Rejected: INC-004 is now its own incident, and it is never deleted
    assert client.get("/api/incidents/INC-004").json()["duplicate_of"] is None


def test_intake_queues_report_for_the_intake_agent(client, session_factory):
    r = client.post("/api/intake/reports", json={"source": "citizen_report", "text": "water flooding river street zone d, people stuck"})
    assert r.status_code == 202
    with session_factory() as db:
        raw = db.get(m.RawReport, r.json()["raw_report_id"])
        assert raw.processing_status == "normalized" and raw.normalized_incident_id
    assert client.post("/api/intake/reports", json={"source": "citizen_report"}).status_code == 422


# ---------------------------------------------------------------- resources & shelters

def test_resource_status_rules(client, session_factory):
    assert client.patch("/api/resources/BOAT-002/status", json={"status": "reserved", "reason": "x"}).status_code == 409
    assert client.patch("/api/resources/BOAT-002/status", json={"status": "available", "reason": "x"}).status_code == 409
    r = client.patch("/api/resources/BOAT-002/status", json={"status": "unavailable", "reason": "Engine failure"},
                     headers={"X-User": "Field Ops"})
    assert r.status_code == 200 and r.json()["status"] == "unavailable"
    history = client.get("/api/resources/BOAT-002/history").json()
    assert history[0]["from_status"] == "available" and "Field Ops" in history[0]["reason"]
    alerts = client.get("/api/alerts").json()
    assert any(a["kind"] == "resource_unavailable" and a["related_id"] == "BOAT-002" for a in alerts)
    assert client.patch("/api/resources/NOPE-1/status", json={"status": "unavailable", "reason": "x"}).status_code == 404


def test_shelter_threshold_alert_fires_once(client):
    def shelter_alerts():
        return [a for a in client.get("/api/alerts").json() if a["kind"] == "shelter_capacity"]

    client.patch("/api/shelters/SHL-A", json={"current_occupancy": 168})   # 84%
    assert shelter_alerts() == []
    client.patch("/api/shelters/SHL-A", json={"current_occupancy": 172})   # 86%: crosses 85%
    assert len(shelter_alerts()) == 1 and shelter_alerts()[0]["severity"] == "warning"
    client.patch("/api/shelters/SHL-A", json={"current_occupancy": 180})   # still above: no repeat
    assert len(shelter_alerts()) == 1
    full = client.patch("/api/shelters/SHL-A", json={"current_occupancy": 200}).json()
    assert full["status"] == "full"
    assert any(a["severity"] == "critical" for a in shelter_alerts())


# ---------------------------------------------------------------- dynamic replanning

def test_road_closure_supersedes_and_replans(client, seeded_run, session_factory):
    """TC-05 at API level: approve INC-002's action, leave others pending, close the road to Zone B."""
    approved = pending_for(client, "INC-002")
    assert client.post(f"/api/actions/{approved['id']}/approve").status_code == 200
    still_pending = {a["id"] for a in client.get("/api/actions?status=pending").json()}

    r = client.patch("/api/roads/RD-B", json={"status": "closed", "provenance": "known", "note": "Canal overflow"})
    assert r.status_code == 200 and r.json()["status"] == "closed"

    alerts = client.get("/api/alerts").json()
    assert any(a["kind"] == "route_blocked" and a["severity"] == "critical" and a["related_id"] == "RD-B" for a in alerts)
    with session_factory() as db:
        notes = db.scalars(select(m.ActionEvent.comment).where(m.ActionEvent.action_id == approved["id"],
                                                               m.ActionEvent.event == "comment")).all()
        assert any("coordinator review required" in n for n in notes)  # approved work is flagged, not changed
        assert db.get(m.Action, approved["id"]).status == "approved"
        runs = db.scalar(select(func.count()).select_from(m.AgentRun))
        assert runs == 2  # the closure triggered a new run
    # The previous pending recommendations were replaced by the new run's
    new_pending = {a["id"] for a in client.get("/api/actions?status=pending").json()}
    assert still_pending.isdisjoint(new_pending)


def test_weather_injection(client, seeded_run):
    body = {"zone": "Zone C", "condition": "Very heavy rain", "rainfall_mm_per_hr": 38, "wind_kph": 55,
            "temperature_c": 25, "forecast_summary": "Very heavy rain", "severe_warning": True,
            "forecast_hourly": [{"hour": h, "precipitation_mm": 36, "wind_kph": 55} for h in range(1, 7)]}
    r = client.post("/api/simulation/weather", json=body).json()
    assert r["worsened"] is True
    inc3 = client.get("/api/incidents/INC-003").json()
    weather_factor = next(f for f in inc3["priority"]["factors"] if f["name"] == "Weather / escalation")
    assert weather_factor["points"] == 15  # 13 + strong wind
    assert any(a["kind"] == "weather_worsening" and "Zone C" in a["message"] for a in client.get("/api/alerts").json())


def test_weather_injection_refused_in_live_mode(client, services):
    services.settings.weather_mode = "live"
    try:
        r = client.post("/api/simulation/weather", json={"zone": "Zone C", "condition": "x", "rainfall_mm_per_hr": 1,
                                                         "wind_kph": 1, "temperature_c": 1, "forecast_summary": "x",
                                                         "severe_warning": False, "forecast_hourly": []})
        assert r.status_code == 409
    finally:
        services.settings.weather_mode = "simulation"


# ---------------------------------------------------------------- actions

def test_action_lifecycle_and_audit_trail(client, seeded_run, session_factory):
    act = pending_for(client, "INC-003")
    hdr = {"X-User": "Asha"}
    approved = client.post(f"/api/actions/{act['id']}/approve", json={"comment": "Proceed"}, headers=hdr).json()
    assert approved["status"] == "approved" and approved["approved_at"]
    with session_factory() as db:
        statuses = {r.id: r.status for r in db.scalars(select(m.Resource).where(m.Resource.id.in_(act["resource_ids"])))}
        assert set(statuses.values()) == {"reserved"}
    assert client.post(f"/api/actions/{act['id']}/complete").status_code == 409   # must start first
    assert client.post(f"/api/actions/{act['id']}/start", headers=hdr).json()["status"] == "in_progress"
    client.post(f"/api/actions/{act['id']}/comment", json={"comment": "Boats launched"}, headers=hdr)
    done = client.post(f"/api/actions/{act['id']}/complete", headers=hdr).json()
    assert done["status"] == "completed" and "Asha: Boats launched" in done["remarks"]
    with session_factory() as db:
        assert {r.status for r in db.scalars(select(m.Resource).where(m.Resource.id.in_(act["resource_ids"])))} == {"returning"}
    events = client.get(f"/api/actions/{act['id']}/events").json()
    assert [e["event"] for e in events] == ["created", "approved", "started", "comment", "completed"]
    assert all(e["actor"] == "Asha" for e in events[1:])
    # Returning -> available is a manual step (back at base) and releases the assignment
    rid = act["resource_ids"][0]
    assert client.patch(f"/api/resources/{rid}/status", json={"status": "available", "reason": "Back at base"}).status_code == 200


def test_reject_and_comment(client, seeded_run):
    act = pending_for(client, "INC-001")
    assert client.post(f"/api/actions/{act['id']}/reject", json={"comment": "Use road convoy"}).json()["status"] == "rejected"
    assert client.post(f"/api/actions/{act['id']}/approve").status_code == 409
    assert client.post(f"/api/actions/{act['id']}/comment", json={}).status_code == 422


def test_modify_validates_resources(client, seeded_run):
    act = pending_for(client, "INC-003")
    bad = client.post(f"/api/actions/{act['id']}/modify", json={"resource_ids": ["AMB-001"]})
    assert bad.status_code == 409 and "known closure of RD-C" in bad.json()["detail"]
    assert client.post(f"/api/actions/{act['id']}/modify", json={"resource_ids": ["BOAT-001"]}).status_code == 409
    assert client.post(f"/api/actions/{act['id']}/modify", json={"resource_ids": ["BOAT-002", "BOAT-002"]}).status_code == 422
    ok = client.post(f"/api/actions/{act['id']}/modify", json={"resource_ids": ["BOAT-002"], "comment": "One boat first"}).json()
    assert ok["resource_ids"] == ["BOAT-002"] and ok["status"] == "pending"


def test_reassess_starts_a_new_run(client, seeded_run, session_factory):
    act = pending_for(client, "INC-002")
    client.post(f"/api/actions/{act['id']}/reassess", json={"comment": "Water level dropping"})
    with session_factory() as db:
        assert db.scalars(select(m.AgentRun.trigger).order_by(m.AgentRun.started_at.desc())).first() == "reassess"
        assert db.get(m.Action, act["id"]).status == "superseded"


def test_double_allocation_rejected_with_409_and_replan(client, seeded_run, session_factory):
    """TC-08 at API level: two pending actions made to share BOAT-002; the second approval gets 409."""
    a3, a2 = pending_for(client, "INC-003"), pending_for(client, "INC-002")
    shared = a3["resource_ids"][0]
    client.post(f"/api/actions/{a2['id']}/modify", json={"resource_ids": [shared]})
    assert client.post(f"/api/actions/{a3['id']}/approve").status_code == 200
    second = client.post(f"/api/actions/{a2['id']}/approve")
    assert second.status_code == 409 and shared in second.json()["detail"]
    with session_factory() as db:
        active = db.scalar(select(func.count()).select_from(m.ResourceAssignment)
                           .where(m.ResourceAssignment.resource_id == shared,
                                  m.ResourceAssignment.status.in_(["reserved", "deployed"])))
        assert active == 1
        assert db.get(m.Action, a2["id"]).status == "superseded"
        assert db.scalar(select(func.count()).select_from(m.AgentRun)) == 2  # 409 also triggered a re-plan


def test_concurrent_approvals_cannot_both_reserve(client, seeded_run, services, session_factory):
    """Two coordinators approve, at the same instant, actions that share one boat. Row locks serialise them."""
    a3, a2 = pending_for(client, "INC-003"), pending_for(client, "INC-002")
    shared = a3["resource_ids"][0]
    client.post(f"/api/actions/{a2['id']}/modify", json={"resource_ids": [shared]})
    barrier, outcomes = threading.Barrier(2), {}

    def approve(action_id: str) -> None:
        with session_factory() as db:
            barrier.wait()
            try:
                action_svc.approve(services, db, action_id, "concurrent", None)
                outcomes[action_id] = "approved"
            except Conflict:
                outcomes[action_id] = "409"

    threads = [threading.Thread(target=approve, args=(aid,)) for aid in (a3["id"], a2["id"])]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(outcomes.values()) == ["409", "approved"]
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(m.ResourceAssignment).where(
            m.ResourceAssignment.resource_id == shared, m.ResourceAssignment.status == "reserved")) == 1


def test_overdue_actions_alert_once(client, seeded_run, services, session_factory):
    act = pending_for(client, "INC-003")
    with session_factory() as db:
        db.get(m.Action, act["id"]).due_by = services.now() - timedelta(minutes=1)
        db.commit()
    assert len(check_overdue(services)) == 1
    assert check_overdue(services) == []
    assert any(a["kind"] == "action_overdue" and a["related_id"] == act["id"] for a in client.get("/api/alerts").json())


# ---------------------------------------------------------------- alerts, reports, websocket, simulation

def test_alert_acknowledge(client, seeded_run):
    alert = client.get("/api/alerts?acknowledged=false").json()[0]
    assert client.post(f"/api/alerts/{alert['id']}/ack").json()["acknowledged"] is True
    assert alert["id"] not in {a["id"] for a in client.get("/api/alerts?acknowledged=false").json()}


def test_situation_report_pdf(client, seeded_run, tmp_path):
    meta = client.post("/api/reports/situation").json()
    assert meta["id"] == "SR-001" and meta["pdf_url"] == "/api/reports/SR-001/pdf" and meta["narrative"]
    pdf = client.get(meta["pdf_url"])
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    path = tmp_path / "r.pdf"
    path.write_bytes(pdf.content)
    text = "\n".join(p.extract_text() for p in PdfReader(path).pages)
    for n, name in enumerate(["Situation summary", "Incident summary", "Affected locations", "Priority assessment",
                              "Weather conditions", "Accessibility", "Available resources", "Deployed",
                              "Resource shortages", "Shelter status", "Recommended actions", "Pending approvals",
                              "completed actions", "Data uncertainties", "Assessment timestamp"], start=1):
        assert f"{n}. " in text and name in text, name
    assert "DECISION SUPPORT ONLY" in text
    assert [r["id"] for r in client.get("/api/reports").json()] == ["SR-001"]


def test_websocket_pushes_events(client, seeded_run):
    with client.websocket_connect("/ws") as ws:
        act = pending_for(client, "INC-002")
        client.post(f"/api/actions/{act['id']}/approve")
        messages = [ws.receive_json() for _ in range(2)]
    assert messages[0] == {"type": "action_updated", "data": {"id": act["id"], "status": "approved"}}
    assert messages[1]["type"] == "situation_changed"


@pytest.mark.parametrize("tc", [f"TC-0{n}" for n in range(1, 9)])
def test_test_case_setups(client, tc):
    r = client.post(f"/api/simulation/scenario/{tc}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run"]["status"] == "completed" and body["run"]["errors"] == []


def test_unknown_test_case(client):
    assert client.post("/api/simulation/scenario/TC-99").status_code == 404


def test_unexpected_error_returns_json_500_with_cors_headers(app):
    """Regression: an unhandled error must not surface in the browser as an opaque 'Failed to fetch'."""
    from fastapi.testclient import TestClient

    def boom():
        raise RuntimeError("simulated bug")

    app.add_api_route("/api/test-boom", boom)
    with TestClient(app) as c:
        r = c.get("/api/test-boom", headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 500 and "RuntimeError" in r.json()["detail"]
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


@pytest.mark.parametrize("origin, allowed", [("http://localhost:5174", True), ("http://127.0.0.1:4173", True),
                                             ("https://evil.example.com", False)])
def test_cors_allows_any_local_dev_port_only(client, origin, allowed):
    r = client.get("/api/health", headers={"Origin": origin})
    assert (r.headers.get("access-control-allow-origin") == origin) is allowed
