"""Application services container, created once per app (tests create their own).

Bundles the session factory, the agent dependencies (Deps: LLM, weather, routing, geocoder) and the
event bus, plus helpers every service needs: committing alerts, broadcasting and starting runs.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import Deps
from app.core.config import Settings
from app.db import models as m
from app.db.repository import next_id
from app.graph.workflow import run_assessment
from app.services import serializers as ser
from app.services.events import EventBus


@dataclass
class Services:
    settings: Settings
    session_factory: Callable[[], Session]
    deps: Deps
    bus: EventBus

    def now(self) -> datetime:
        return self.deps.clock()

    # ---------------------------------------------------------------- alerts

    def add_alert(self, db: Session, kind: str, severity: str, message: str, related_id: str | None) -> m.Alert:
        """Stage an alert in the caller's transaction. Call broadcast_alerts() after commit."""
        alert = m.Alert(id=next_id(db, m.Alert, "ALT"), kind=kind, severity=severity, message=message,
                        related_id=related_id, created_at=self.now(), acknowledged=False)
        db.add(alert)
        db.flush()
        return alert

    def broadcast_alerts(self, alerts: list[m.Alert]) -> None:
        for a in alerts:
            self.bus.publish("alert", ser.alert(a))

    # ---------------------------------------------------------------- runs

    def run_now(self, trigger: str) -> dict[str, Any]:
        """Run the agent workflow synchronously and broadcast what changed."""
        started = self.now()
        self.bus.publish("run_started", {"trigger": trigger})
        result = run_assessment(trigger, self.deps)
        with self.session_factory() as db:
            new_alerts = db.scalars(select(m.Alert).where(m.Alert.created_at >= started).order_by(m.Alert.id)).all()
        self.broadcast_alerts(list(new_alerts))
        self.bus.publish("run_finished", {k: result[k] for k in ("run_id", "status", "recommended_plan_id",
                                                                 "pending_actions", "summary")})
        self.bus.publish("situation_changed", {"reason": f"run {result['run_id']} ({trigger})"})
        return result

    def schedule_run(self, trigger: str, background: BackgroundTasks | None) -> None:
        """Start a run after the response is sent (or immediately when no BackgroundTasks is available)."""
        if background is None:
            self.run_now(trigger)
        else:
            background.add_task(self.run_now, trigger)
