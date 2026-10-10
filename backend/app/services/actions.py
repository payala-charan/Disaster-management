"""Human-in-the-loop action workflow. Only these coordinator calls change an action's state:

    pending --approve--> approved --start--> in_progress --complete--> completed
    pending --reject--> rejected          pending --modify--> pending (different resources)
    any open --reassess--> (new agent run; pending actions get superseded by it)
    any --comment--> (audit note only)

Approve is where resources are actually reserved, and it is protected twice against double allocation:
1. Row locks: `SELECT ... FROM resources WHERE id IN (...) ORDER BY id FOR UPDATE`. Two concurrent
   approvals touching the same resource are serialised, and the second one sees the first's result.
   (Locking in ID order means two approvals can never wait on each other in a deadlock.)
2. Database constraint: the partial unique index on active assignments rejects a second
   reserved/deployed assignment even if application code had a bug.
Either way the coordinator gets HTTP 409, the stale action is superseded, and a new run re-plans.
Every step writes an action_events row (who, what, when, comment).
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import models as m
from app.db.repository import ACTIVE_ASSIGNMENT, road_facts
from app.engines.access import assess_zone_access
from app.engines.resources import InvalidTransition, validate_transition
from app.services.context import Services
from app.services.errors import Conflict, Invalid, get_or_404


class ApprovalConflict(Conflict):
    """Resources are no longer available. The action was superseded and a re-plan is needed."""


def _event(db: Session, svc: Services, action: m.Action, event: str, actor: str, comment: str | None = None,
           payload: dict | None = None) -> None:
    db.add(m.ActionEvent(action_id=action.id, event=event, actor=actor, comment=comment, payload=payload, at=svc.now()))


def _require(action: m.Action, *allowed: str) -> None:
    if action.status not in allowed:
        raise Conflict(f"{action.id} is '{action.status}'; this step needs {' or '.join(allowed)}")


def _advance(db: Session, svc: Services, action: m.Action, from_status: str, to_status: str, reason: str) -> None:
    """Move this action's assignments (and their resources) from one stage to the next. Only resources
    still assigned to the action move: one that broke down meanwhile was released and stays as it is."""
    assignments = db.scalars(select(m.ResourceAssignment).where(m.ResourceAssignment.action_id == action.id,
                                                                m.ResourceAssignment.status == from_status)).all()
    if not assignments:
        raise Conflict(f"{action.id} has no '{from_status}' resources left; request reassessment instead")
    ids = sorted(a.resource_id for a in assignments)
    try:
        for res in db.scalars(select(m.Resource).where(m.Resource.id.in_(ids)).order_by(m.Resource.id).with_for_update()):
            validate_transition(res.status, to_status)
            db.add(m.ResourceStatusHistory(resource_id=res.id, from_status=res.status, to_status=to_status,
                                           reason=reason, changed_at=svc.now()))
            res.status = to_status
    except InvalidTransition as exc:
        raise Conflict(str(exc)) from exc
    for a in assignments:
        a.status = to_status


def _done(svc: Services, db: Session, action: m.Action) -> m.Action:
    db.commit()
    svc.bus.publish("action_updated", {"id": action.id, "status": action.status})
    svc.bus.publish("situation_changed", {})
    return action


def approve(svc: Services, db: Session, action_id: str, actor: str, comment: str | None) -> m.Action:
    action = get_or_404(db, m.Action, action_id, for_update=True)
    _require(action, "pending")
    ids = sorted(set(action.resource_ids))
    rows = db.scalars(select(m.Resource).where(m.Resource.id.in_(ids)).order_by(m.Resource.id).with_for_update()).all()
    active = dict(db.execute(select(m.ResourceAssignment.resource_id, m.ResourceAssignment.incident_id)
                             .where(m.ResourceAssignment.resource_id.in_(ids),
                                    m.ResourceAssignment.status.in_(ACTIVE_ASSIGNMENT))).all())
    problems = [f"{rid} does not exist" for rid in sorted(set(ids) - {r.id for r in rows})]
    problems += [f"{r.id} is {r.status}" + (f" (assigned to {active[r.id]})" if r.id in active else "")
                 for r in rows if r.status != "available" or r.id in active]
    if problems:
        _supersede_conflicted(svc, db, action, actor, problems)
        raise ApprovalConflict(f"Cannot approve {action.id}: {'; '.join(problems)}. "
                               "The action was superseded and a new recommendation is being prepared.")

    for res in rows:
        validate_transition(res.status, "reserved")
        db.add(m.ResourceStatusHistory(resource_id=res.id, from_status=res.status, to_status="reserved",
                                       reason=f"Reserved for {action.id} (approved by {actor})", changed_at=svc.now()))
        res.status = "reserved"
        db.add(m.ResourceAssignment(resource_id=res.id, incident_id=action.incident_id, action_id=action.id,
                                    status="reserved", created_at=svc.now()))
    action.status, action.approved_at = "approved", svc.now()
    _event(db, svc, action, "approved", actor, comment)
    try:
        db.flush()
    except IntegrityError as exc:  # second guard: partial unique index on active assignments
        db.rollback()
        action = get_or_404(db, m.Action, action_id, for_update=True)
        _supersede_conflicted(svc, db, action, actor, ["a resource was reserved concurrently"])
        raise ApprovalConflict(f"Cannot approve {action_id}: a resource was reserved by another approval "
                               "(database constraint). A new recommendation is being prepared.") from exc
    return _done(svc, db, action)


def _supersede_conflicted(svc: Services, db: Session, action: m.Action, actor: str, problems: list[str]) -> None:
    action.status = "superseded"
    _event(db, svc, action, "superseded", "system",
           f"Approval by {actor} refused: {'; '.join(problems)}", {"problems": problems})
    db.commit()
    svc.bus.publish("action_updated", {"id": action.id, "status": action.status})


def reject(svc: Services, db: Session, action_id: str, actor: str, comment: str | None) -> m.Action:
    action = get_or_404(db, m.Action, action_id, for_update=True)
    _require(action, "pending")
    action.status = "rejected"
    _event(db, svc, action, "rejected", actor, comment)
    return _done(svc, db, action)


def modify(svc: Services, db: Session, action_id: str, actor: str, resource_ids: list[str],
           comment: str | None) -> m.Action:
    """Replace the recommended resources with others the coordinator chose. They must exist, be available
    and unassigned, and be able to reach the incident (no ground units through a known closure)."""
    action = get_or_404(db, m.Action, action_id, for_update=True)
    _require(action, "pending")
    if not resource_ids or len(set(resource_ids)) != len(resource_ids):
        raise Invalid("resource_ids must be a non-empty list without duplicates")
    rows = {r.id: r for r in db.scalars(select(m.Resource).where(m.Resource.id.in_(resource_ids)))}
    active = set(db.scalars(select(m.ResourceAssignment.resource_id).where(
        m.ResourceAssignment.resource_id.in_(resource_ids), m.ResourceAssignment.status.in_(ACTIVE_ASSIGNMENT))))
    incident = db.get(m.Incident, action.incident_id)
    access = assess_zone_access(incident.zone, road_facts(db))
    problems = []
    for rid in resource_ids:
        r = rows.get(rid)
        if r is None:
            problems.append(f"{rid} does not exist")
        elif r.status != "available" or rid in active:
            problems.append(f"{rid} is not available ({r.status})")
        elif not access.ground_access_possible and "water_rescue" not in r.capabilities:
            problems.append(f"{rid} cannot reach {incident.zone} by road (known closure of {access.deciding_road_id})")
    if problems:
        raise Conflict("; ".join(problems))
    old = list(action.resource_ids)
    action.resource_ids = list(resource_ids)
    action.description = f"Consider deploying {', '.join(resource_ids)} to {incident.id} ({incident.zone}): {incident.title}"
    action.rationale = [*action.rationale, f"Resources changed by {actor}: {', '.join(old)} -> {', '.join(resource_ids)}"]
    _event(db, svc, action, "modified", actor, comment, {"old": old, "new": list(resource_ids)})
    return _done(svc, db, action)


def request_reassessment(svc: Services, db: Session, action_id: str, actor: str, comment: str | None) -> m.Action:
    action = get_or_404(db, m.Action, action_id)
    _require(action, "pending", "approved", "in_progress")
    _event(db, svc, action, "reassess_requested", actor, comment)
    return _done(svc, db, action)


def add_comment(svc: Services, db: Session, action_id: str, actor: str, comment: str | None) -> m.Action:
    if not comment:
        raise Invalid("comment is required")
    action = get_or_404(db, m.Action, action_id)
    _event(db, svc, action, "comment", actor, comment)
    return _done(svc, db, action)


def start(svc: Services, db: Session, action_id: str, actor: str, comment: str | None) -> m.Action:
    action = get_or_404(db, m.Action, action_id, for_update=True)
    _require(action, "approved")
    _advance(db, svc, action, "reserved", "deployed", f"Deployed for {action.id} ({actor})")
    action.status, action.started_at = "in_progress", svc.now()
    _event(db, svc, action, "started", actor, comment)
    return _done(svc, db, action)


def complete(svc: Services, db: Session, action_id: str, actor: str, comment: str | None) -> m.Action:
    action = get_or_404(db, m.Action, action_id, for_update=True)
    _require(action, "in_progress")
    _advance(db, svc, action, "deployed", "returning", f"Task {action.id} completed ({actor})")
    action.status, action.completed_at = "completed", svc.now()
    _event(db, svc, action, "completed", actor, comment)
    return _done(svc, db, action)
