"""Incidents, duplicate review and raw-report intake."""

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_actor, get_db, get_services, replan_if_needed
from app.db import models as m
from app.models import schemas as s
from app.services import operations as ops
from app.services import serializers as ser
from app.services.context import Services
from app.services.errors import NotFound, get_or_404

router = APIRouter()


def _one(db: Session, incident_id: str) -> s.Incident:
    found = ser.incidents(db, ids=[incident_id])
    if not found:
        raise NotFound(f"Incident {incident_id} not found")
    return found[0]


@router.get("/incidents", response_model=list[s.Incident])
def list_incidents(include_resolved: bool = False, db: Session = Depends(get_db)):
    return ser.incidents(db, include_resolved=include_resolved)


@router.post("/incidents", response_model=s.Incident, status_code=status.HTTP_201_CREATED)
def create_incident(body: s.IncidentCreate, background: BackgroundTasks, svc: Services = Depends(get_services),
                    db: Session = Depends(get_db), actor: str = Depends(get_actor)):
    incident_id = ops.create_incident(svc, db, body, actor)
    svc.schedule_run("new_report", background)
    return _one(db, incident_id)


@router.get("/incidents/{incident_id}", response_model=s.Incident)
def get_incident(incident_id: str, db: Session = Depends(get_db)):
    return _one(db, incident_id)


@router.patch("/incidents/{incident_id}", response_model=s.Incident)
def patch_incident(incident_id: str, body: s.IncidentPatch, background: BackgroundTasks,
                   svc: Services = Depends(get_services), db: Session = Depends(get_db), actor: str = Depends(get_actor)):
    impact = ops.patch_incident(svc, db, incident_id, body, actor)
    replan_if_needed(svc, impact, "reassess", background)
    return _one(db, incident_id)


@router.get("/incidents/{incident_id}/duplicates", response_model=list[s.DuplicateLink])
def duplicates(incident_id: str, db: Session = Depends(get_db)):
    get_or_404(db, m.Incident, incident_id)
    links = db.scalars(select(m.DuplicateLink).where(or_(m.DuplicateLink.incident_id == incident_id,
                                                         m.DuplicateLink.candidate_id == incident_id))
                       .order_by(m.DuplicateLink.score.desc()))
    return [ser.duplicate_link(d) for d in links]


@router.post("/duplicates/{link_id}/{decision}", response_model=s.DuplicateLink)
def review_duplicate(link_id: str, decision: str, background: BackgroundTasks, svc: Services = Depends(get_services),
                     db: Session = Depends(get_db), actor: str = Depends(get_actor)):
    states = {"confirm": "confirmed", "reject": "rejected"}
    if decision not in states:
        raise NotFound("Use /confirm or /reject")
    link = ops.review_duplicate(svc, db, link_id, states[decision], actor)
    svc.schedule_run("reassess", background)  # allocation depends on which reports are the same event
    return ser.duplicate_link(link)


@router.post("/intake/reports", status_code=status.HTTP_202_ACCEPTED)
def intake(body: s.IntakeReportIn, background: BackgroundTasks, svc: Services = Depends(get_services),
           db: Session = Depends(get_db)) -> dict[str, Any]:
    """Accept a raw report from any source. The Intake agent normalizes it in the run that follows."""
    raw_id = ops.submit_report(svc, db, body)
    svc.schedule_run("new_report", background)
    return {"raw_report_id": raw_id, "status": "received", "message": "Report queued for the Intake agent"}
