"""Background check (every OVERDUE_CHECK_S seconds): actions whose `due_by` has passed while still
pending or approved (not started) raise ONE action_overdue alert each."""

from sqlalchemy import select

from app.db import models as m
from app.services.context import Services


def check_overdue(svc: Services) -> list[str]:
    now = svc.now()
    with svc.session_factory() as db:
        already = set(db.scalars(select(m.Alert.related_id).where(m.Alert.kind == "action_overdue")))
        overdue = db.scalars(select(m.Action).where(m.Action.status.in_(["pending", "approved"]),
                                                    m.Action.due_by < now)).all()
        alerts = []
        for a in overdue:
            if a.id in already:
                continue
            waiting = "approval" if a.status == "pending" else "start"
            alerts.append(svc.add_alert(db, "action_overdue", "warning",
                                        f"{a.id} ({a.priority}) for {a.incident_id} is overdue: awaiting {waiting} "
                                        f"since {a.due_by.strftime('%H:%M UTC')}", a.id))
        db.commit()
    svc.broadcast_alerts(alerts)
    return [a.id for a in alerts]
