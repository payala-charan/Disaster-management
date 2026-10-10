"""Resources, assessment runs, plans, actions, alerts and situation reports."""

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.api.deps import get_actor, get_db, get_services, replan_if_needed
from app.db import models as m
from app.models import schemas as s
from app.services import actions as action_svc
from app.services import operations as ops
from app.services import reports
from app.services import serializers as ser
from app.services.context import Services
from app.services.errors import NotFound, get_or_404

router = APIRouter()


# ---------------------------------------------------------------- resources

@router.get("/resources", response_model=list[s.Resource])
def resources(db: Session = Depends(get_db)):
    return ser.resources(db)


@router.patch("/resources/{resource_id}/status", response_model=s.Resource)
def resource_status(resource_id: str, body: s.ResourceStatusPatch, background: BackgroundTasks,
                    svc: Services = Depends(get_services), db: Session = Depends(get_db), actor: str = Depends(get_actor)):
    impact = ops.change_resource_status(svc, db, resource_id, body, actor)
    replan_if_needed(svc, impact, "resource_change", background)
    return next(r for r in ser.resources(db) if r.id == resource_id)


@router.get("/resources/{resource_id}/history", response_model=list[s.ResourceStatusChange])
def resource_history(resource_id: str, db: Session = Depends(get_db)):
    return ops.resource_history(db, resource_id)


# ---------------------------------------------------------------- assessment runs & plans

@router.post("/assessments/run", response_model=s.RunResult)
def run(svc: Services = Depends(get_services)):
    """Run all agents now and wait for the result (typically well under a second in simulation mode)."""
    result = svc.run_now("manual")
    return s.RunResult(**{k: result[k] for k in s.RunResult.model_fields})


@router.get("/assessments", response_model=list[s.AgentRun])
def runs(limit: int = 20, db: Session = Depends(get_db)):
    rows = db.scalars(select(m.AgentRun).order_by(m.AgentRun.started_at.desc()).limit(limit)).all()
    steps: dict[str, list[m.AgentStep]] = {}
    for st in db.scalars(select(m.AgentStep).where(m.AgentStep.run_id.in_([r.id for r in rows])).order_by(m.AgentStep.id)):
        steps.setdefault(st.run_id, []).append(st)
    return [ser.agent_run(r, steps.get(r.id, [])) for r in rows]


@router.get("/assessments/{run_id}", response_model=s.AgentRun)
def run_detail(run_id: str, db: Session = Depends(get_db)):
    run_row = get_or_404(db, m.AgentRun, run_id)
    steps = db.scalars(select(m.AgentStep).where(m.AgentStep.run_id == run_id).order_by(m.AgentStep.id)).all()
    return ser.agent_run(run_row, list(steps))


@router.get("/plans/latest", response_model=list[s.Plan])
def latest_plans(db: Session = Depends(get_db)):
    latest_run = db.scalar(select(m.Plan.run_id).order_by(m.Plan.created_at.desc(), m.Plan.id.desc()).limit(1))
    if latest_run is None:
        return []
    return [ser.plan(p) for p in db.scalars(select(m.Plan).where(m.Plan.run_id == latest_run).order_by(m.Plan.variant))]


@router.get("/plans/{plan_id}", response_model=s.Plan)
def plan(plan_id: str, db: Session = Depends(get_db)):
    return ser.plan(get_or_404(db, m.Plan, plan_id))


# ---------------------------------------------------------------- actions (human approval)

@router.get("/actions", response_model=list[s.ResponseAction])
def actions(status: str | None = None, db: Session = Depends(get_db)):
    return ser.actions(db, status.split(",") if status else None)


@router.get("/actions/{action_id}", response_model=s.ResponseAction)
def action(action_id: str, db: Session = Depends(get_db)):
    row = get_or_404(db, m.Action, action_id)
    events = db.scalars(select(m.ActionEvent).where(m.ActionEvent.action_id == action_id).order_by(m.ActionEvent.at)).all()
    return ser.action(row, list(events))


@router.get("/actions/{action_id}/events", response_model=list[s.ActionEvent])
def action_events(action_id: str, db: Session = Depends(get_db)):
    get_or_404(db, m.Action, action_id)
    return [ser.action_event(e) for e in db.scalars(select(m.ActionEvent).where(m.ActionEvent.action_id == action_id)
                                                     .order_by(m.ActionEvent.at, m.ActionEvent.id))]


def _action_response(db: Session, row: m.Action) -> s.ResponseAction:
    return action(row.id, db)


@router.post("/actions/{action_id}/approve", response_model=s.ResponseAction)
def approve(action_id: str, body: s.ActionDecision | None = None, svc: Services = Depends(get_services),
            db: Session = Depends(get_db), actor: str = Depends(get_actor)):
    try:
        row = action_svc.approve(svc, db, action_id, actor, body.comment if body else None)
    except action_svc.ApprovalConflict as exc:
        # 409 AND a re-plan: the recommendation was based on resources that are no longer free.
        return JSONResponse(status_code=409, content={"detail": exc.detail},
                            background=BackgroundTask(svc.run_now, "resource_change"))
    return _action_response(db, row)


@router.post("/actions/{action_id}/reject", response_model=s.ResponseAction)
def reject(action_id: str, body: s.ActionDecision | None = None, svc: Services = Depends(get_services),
           db: Session = Depends(get_db), actor: str = Depends(get_actor)):
    return _action_response(db, action_svc.reject(svc, db, action_id, actor, body.comment if body else None))


@router.post("/actions/{action_id}/modify", response_model=s.ResponseAction)
def modify(action_id: str, body: s.ActionModify, svc: Services = Depends(get_services),
           db: Session = Depends(get_db), actor: str = Depends(get_actor)):
    return _action_response(db, action_svc.modify(svc, db, action_id, actor, body.resource_ids, body.comment))


@router.post("/actions/{action_id}/reassess", response_model=s.ResponseAction)
def reassess(action_id: str, background: BackgroundTasks, body: s.ActionDecision | None = None,
             svc: Services = Depends(get_services), db: Session = Depends(get_db), actor: str = Depends(get_actor)):
    row = action_svc.request_reassessment(svc, db, action_id, actor, body.comment if body else None)
    svc.schedule_run("reassess", background)
    return _action_response(db, row)


@router.post("/actions/{action_id}/comment", response_model=s.ResponseAction)
def comment(action_id: str, body: s.ActionDecision, svc: Services = Depends(get_services),
            db: Session = Depends(get_db), actor: str = Depends(get_actor)):
    return _action_response(db, action_svc.add_comment(svc, db, action_id, actor, body.comment))


@router.post("/actions/{action_id}/start", response_model=s.ResponseAction)
def start(action_id: str, body: s.ActionDecision | None = None, svc: Services = Depends(get_services),
          db: Session = Depends(get_db), actor: str = Depends(get_actor)):
    return _action_response(db, action_svc.start(svc, db, action_id, actor, body.comment if body else None))


@router.post("/actions/{action_id}/complete", response_model=s.ResponseAction)
def complete(action_id: str, body: s.ActionDecision | None = None, svc: Services = Depends(get_services),
             db: Session = Depends(get_db), actor: str = Depends(get_actor)):
    return _action_response(db, action_svc.complete(svc, db, action_id, actor, body.comment if body else None))


# ---------------------------------------------------------------- alerts

@router.get("/alerts", response_model=list[s.Alert])
def alerts(acknowledged: bool | None = None, limit: int = 100, db: Session = Depends(get_db)):
    query = select(m.Alert).order_by(m.Alert.created_at.desc(), m.Alert.id.desc()).limit(limit)
    if acknowledged is not None:
        query = query.where(m.Alert.acknowledged == acknowledged)
    return [ser.alert(a) for a in db.scalars(query)]


@router.post("/alerts/{alert_id}/ack", response_model=s.Alert)
def ack(alert_id: str, svc: Services = Depends(get_services), db: Session = Depends(get_db)):
    row = get_or_404(db, m.Alert, alert_id)
    row.acknowledged = True
    db.commit()
    svc.bus.publish("situation_changed", {})
    return ser.alert(row)


# ---------------------------------------------------------------- situation reports

@router.post("/reports/situation", response_model=s.SituationReportDetail, status_code=201)
def generate_report(svc: Services = Depends(get_services), db: Session = Depends(get_db)):
    return reports.meta(reports.generate(svc, db))


@router.get("/reports", response_model=list[s.SituationReportDetail])
def list_reports(db: Session = Depends(get_db)):
    return reports.list_reports(db)


@router.get("/reports/{report_id}", response_model=s.SituationReportDetail)
def report(report_id: str, db: Session = Depends(get_db)):
    return reports.meta(get_or_404(db, m.SituationReport, report_id))


@router.get("/reports/{report_id}/pdf")
def report_pdf(report_id: str, svc: Services = Depends(get_services), db: Session = Depends(get_db)):
    row = get_or_404(db, m.SituationReport, report_id)
    path = svc.settings.reports_dir / row.file_name
    if not path.exists():
        raise NotFound(f"PDF for {report_id} is no longer on disk; generate a new report")
    return FileResponse(path, media_type="application/pdf", filename=row.file_name)
