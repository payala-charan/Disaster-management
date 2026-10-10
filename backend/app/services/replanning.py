"""Dynamic replanning: the one path every changed condition goes through.

handle_change():
1. records a condition_changes row (what, old -> new, provenance),
2. asks the pure impact engine (engines/replanning.py) what is affected,
3. marks affected PENDING actions 'superseded' (audit event explains why),
4. adds a review note to affected APPROVED / IN-PROGRESS actions. They are never changed
   automatically, because they are human decisions, so the coordinator decides,
5. returns the impact. The caller commits and, if impact.trigger_run, starts a new agent run, which
   reassesses priorities and publishes an updated recommendation.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as m
from app.db.repository import OPEN_ACTION, next_id
from app.engines.replanning import ActionRef, ConditionChangeEvent, IncidentRef, ReplanImpact, analyze_change
from app.services.context import Services


def handle_change(svc: Services, db: Session, *, kind: str, entity_id: str, entity_name: str, field: str,
                  old: str | None, new: str | None, provenance: str, zone: str | None = None,
                  worsened: bool = False) -> ReplanImpact:
    now = svc.now()
    db.add(m.ConditionChange(id=next_id(db, m.ConditionChange, "CHG"), entity=entity_name, entity_id=entity_id,
                             field=field, old_value=str(old), new_value=str(new), changed_at=now, provenance=provenance))

    incidents = [IncidentRef(id=i.id, zone=i.zone, status=i.status, evacuation_requested="evacuation" in i.assistance_requested)
                 for i in db.scalars(select(m.Incident))]
    open_actions = db.scalars(select(m.Action).where(m.Action.status.in_(OPEN_ACTION))).all()
    impact = analyze_change(
        ConditionChangeEvent(kind=kind, entity_id=entity_id, zone=zone, old_value=old, new_value=new, worsened=worsened),
        incidents,
        [ActionRef(id=a.id, incident_id=a.incident_id, resource_ids=a.resource_ids, status=a.status) for a in open_actions],
    )
    by_id = {a.id: a for a in open_actions}
    reason = f"{entity_name}: {field} {old} -> {new}"
    for aid in impact.supersede_action_ids:
        by_id[aid].status = "superseded"
        db.add(m.ActionEvent(action_id=aid, event="superseded", actor="system", at=now,
                             comment=f"Superseded after a changed condition ({reason}); a new recommendation will follow"))
    for aid in impact.review_action_ids:
        db.add(m.ActionEvent(action_id=aid, event="comment", actor="system", at=now,
                             comment=f"Conditions changed ({reason}): coordinator review required"))
    db.flush()
    return impact
