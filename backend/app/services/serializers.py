"""Rows -> API contract objects (app/models/schemas.py == frontend/src/types/index.ts).

Derived fields are computed here, never stored:
- Incident.priority                 latest priority_assessments row
- Incident.duplicate_of             highest-scoring non-rejected potential_duplicate link
- Incident.related_reports          every non-rejected link (either direction)
- Incident.assigned_resource_ids    active (reserved/deployed) assignments
- Resource.assigned_incident_id     active assignment
- ResponseAction.remarks            comments from the action's audit trail
- SituationSnapshot.shortages       shortages of the current recommended plan
An unknown incident location is exposed as null (never a placeholder point).
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as m
from app.db.geo import geom_to_path, geom_to_point
from app.db.repository import ACTIVE_ASSIGNMENT
from app.models import schemas as s

REMARK_EVENTS = ("comment", "rejected", "modified", "reassess_requested")


def _latest_priorities(db: Session) -> dict[str, m.PriorityAssessmentRow]:
    latest: dict[str, m.PriorityAssessmentRow] = {}
    for row in db.scalars(select(m.PriorityAssessmentRow).order_by(m.PriorityAssessmentRow.assessed_at,
                                                                   m.PriorityAssessmentRow.id)):
        latest[row.incident_id] = row
    return latest


def priority(row: m.PriorityAssessmentRow | None) -> s.PriorityAssessment | None:
    if row is None:
        return None
    return s.PriorityAssessment(score=row.score, level=row.level, factors=row.factors, reasons=row.reasons,
                                verification_needed=row.verification_needed, assessed_at=row.assessed_at)


def incidents(db: Session, ids: list[str] | None = None, include_resolved: bool = True) -> list[s.Incident]:
    query = select(m.Incident).order_by(m.Incident.id)
    if ids is not None:
        query = query.where(m.Incident.id.in_(ids))
    if not include_resolved:
        query = query.where(m.Incident.status != "resolved")
    rows = db.scalars(query).all()
    latest = _latest_priorities(db)
    links = db.scalars(select(m.DuplicateLink).where(m.DuplicateLink.status != "rejected")
                       .order_by(m.DuplicateLink.score.desc())).all()
    assigned: dict[str, list[str]] = {}
    for rid, iid in db.execute(select(m.ResourceAssignment.resource_id, m.ResourceAssignment.incident_id)
                               .where(m.ResourceAssignment.status.in_(ACTIVE_ASSIGNMENT))
                               .order_by(m.ResourceAssignment.resource_id)):
        assigned.setdefault(iid, []).append(rid)

    out = []
    for r in rows:
        dup = next((lk.candidate_id for lk in links
                    if lk.incident_id == r.id and lk.classification == "potential_duplicate"), None)
        related = sorted({lk.candidate_id if lk.incident_id == r.id else lk.incident_id
                          for lk in links if r.id in (lk.incident_id, lk.candidate_id)})
        out.append(s.Incident(
            id=r.id, type=r.type, title=r.title, description=r.description, zone=r.zone,
            location=geom_to_point(r.geom), reported_at=r.reported_at,
            source=r.source, people_affected=r.people_affected, assistance_requested=r.assistance_requested,
            infrastructure_issues=r.infrastructure_issues, confidence=r.confidence,
            verification_status=r.verification_status, duplicate_of=dup, related_reports=related,
            supporting_info=r.supporting_info, priority=priority(latest.get(r.id)),
            assigned_resource_ids=assigned.get(r.id, []), status=r.status,
        ))
    return out


def resources(db: Session) -> list[s.Resource]:
    active = dict(db.execute(select(m.ResourceAssignment.resource_id, m.ResourceAssignment.incident_id)
                             .where(m.ResourceAssignment.status.in_(ACTIVE_ASSIGNMENT))).all())
    return [s.Resource(id=r.id, type=r.type, name=r.name, base_name=r.base_name, location=geom_to_point(r.geom),
                       status=r.status, capacity=r.capacity, capabilities=r.capabilities,
                       assigned_incident_id=active.get(r.id), expected_available_at=r.expected_available_at)
            for r in db.scalars(select(m.Resource).order_by(m.Resource.id))]


def shelters(db: Session) -> list[s.Shelter]:
    return [s.Shelter(id=r.id, name=r.name, location=geom_to_point(r.geom), capacity=r.capacity,
                      current_occupancy=r.current_occupancy, status=r.status)
            for r in db.scalars(select(m.Shelter).order_by(m.Shelter.id))]


def roads(db: Session) -> list[s.RoadSegment]:
    return [s.RoadSegment(id=r.id, name=r.name, serves_zone=r.serves_zone, status=r.status, provenance=r.provenance,
                          path=geom_to_path(r.geom), updated_at=r.updated_at, note=r.note)
            for r in db.scalars(select(m.RoadSegment).order_by(m.RoadSegment.id))]


def weather(db: Session) -> list[s.WeatherSnapshot]:
    latest: dict[str, m.WeatherSnapshot] = {}
    for row in db.scalars(select(m.WeatherSnapshot).order_by(m.WeatherSnapshot.fetched_at, m.WeatherSnapshot.id)):
        latest[row.zone] = row
    return [s.WeatherSnapshot(zone=w.zone, condition=w.condition, rainfall_mm_per_hr=w.rainfall_mm_per_hr,
                              wind_kph=w.wind_kph, temperature_c=w.temperature_c, forecast_summary=w.forecast_summary,
                              severe_warning=w.severe_warning, source=w.source, fetched_at=w.fetched_at)
            for w in sorted(latest.values(), key=lambda w: w.zone)]


def action(row: m.Action, events: list[m.ActionEvent]) -> s.ResponseAction:
    remarks = [f"{e.actor}: {e.comment}" for e in events if e.event in REMARK_EVENTS and e.comment]
    return s.ResponseAction(
        id=row.id, incident_id=row.incident_id, plan_id=row.plan_id, resource_ids=row.resource_ids,
        description=row.description, priority=row.priority, status=row.status, rationale=row.rationale,
        assumptions=row.assumptions, inputs_used=row.inputs_used, created_at=row.created_at,
        approved_at=row.approved_at, started_at=row.started_at, completed_at=row.completed_at,
        due_by=row.due_by, remarks=remarks,
    )


def actions(db: Session, status: list[str] | None = None) -> list[s.ResponseAction]:
    query = select(m.Action).order_by(m.Action.created_at.desc(), m.Action.id.desc())
    if status:
        query = query.where(m.Action.status.in_(status))
    rows = db.scalars(query).all()
    events: dict[str, list[m.ActionEvent]] = {}
    for e in db.scalars(select(m.ActionEvent).where(m.ActionEvent.action_id.in_([r.id for r in rows]))
                        .order_by(m.ActionEvent.at, m.ActionEvent.id)):
        events.setdefault(e.action_id, []).append(e)
    return [action(r, events.get(r.id, [])) for r in rows]


def action_event(e: m.ActionEvent) -> s.ActionEvent:
    return s.ActionEvent(id=e.id, action_id=e.action_id, event=e.event, actor=e.actor, comment=e.comment,
                         payload=e.payload, at=e.at)


def alert(a: m.Alert) -> s.Alert:
    return s.Alert(id=a.id, kind=a.kind, severity=a.severity, message=a.message, related_id=a.related_id,
                   created_at=a.created_at, acknowledged=a.acknowledged)


def change(c: m.ConditionChange) -> s.ConditionChange:
    return s.ConditionChange(id=c.id, entity=c.entity, field=c.field, old_value=c.old_value, new_value=c.new_value,
                             changed_at=c.changed_at, provenance=c.provenance)


def plan(p: m.Plan) -> s.Plan:
    return s.Plan(id=p.id, run_id=p.run_id, variant=p.variant, title=p.title, strategy=p.strategy,
                  allocations=p.allocations, shortages=p.shortages, explanation=p.explanation,
                  assumptions=p.assumptions, critic_findings=p.critic_findings, plan_score=p.plan_score,
                  is_recommended=p.is_recommended, status=p.status, created_at=p.created_at)


def current_recommended_plan(db: Session) -> m.Plan | None:
    return db.scalars(select(m.Plan).where(m.Plan.status == "proposed", m.Plan.is_recommended)
                      .order_by(m.Plan.created_at.desc())).first()


def duplicate_link(d: m.DuplicateLink) -> s.DuplicateLink:
    return s.DuplicateLink(id=d.id, incident_id=d.incident_id, candidate_id=d.candidate_id, score=d.score,
                           classification=d.classification, breakdown=d.breakdown, status=d.status,
                           reviewed_by=d.reviewed_by, reviewed_at=d.reviewed_at, created_at=d.created_at)


def agent_run(run: m.AgentRun, steps: list[m.AgentStep]) -> s.AgentRun:
    return s.AgentRun(
        id=run.id, trigger=run.trigger, started_at=run.started_at, finished_at=run.finished_at, status=run.status,
        steps=[s.AgentStep(agent=st.agent, started_at=st.started_at, duration_ms=st.duration_ms,
                           input_summary=st.input_summary, output_summary=st.output_summary,
                           tool_calls=st.tool_calls, llm_used=st.llm_used, error=st.error) for st in steps],
    )


def situation(db: Session) -> s.SituationSnapshot:
    recommended = current_recommended_plan(db)
    return s.SituationSnapshot(
        incidents=incidents(db, include_resolved=False),
        resources=resources(db),
        shelters=shelters(db),
        roads=roads(db),
        weather=weather(db),
        actions=actions(db, ["pending", "approved", "in_progress", "completed", "rejected"]),
        alerts=[alert(a) for a in db.scalars(select(m.Alert).order_by(m.Alert.created_at.desc(), m.Alert.id.desc())
                                             .limit(50))],
        shortages=recommended.shortages if recommended else [],
        changes=[change(c) for c in db.scalars(select(m.ConditionChange)
                                               .order_by(m.ConditionChange.changed_at.desc(), m.ConditionChange.id.desc())
                                               .limit(20))],
        generated_at=datetime.now(timezone.utc),
    )
