"""FastAPI dependencies shared by all routers."""

from collections.abc import Iterator

from fastapi import BackgroundTasks, Header, Request
from sqlalchemy.orm import Session

from app.engines.replanning import ReplanImpact
from app.services.context import Services


def get_services(request: Request) -> Services:
    return request.app.state.services


def get_db(request: Request) -> Iterator[Session]:
    db = request.app.state.services.session_factory()
    try:
        yield db
    finally:
        db.close()


def get_actor(request: Request, x_user: str | None = Header(default=None)) -> str:
    """Demo identity: the X-User header is recorded in every audit event. Real authentication is future work."""
    return (x_user or request.app.state.services.settings.default_actor).strip()[:64]


def replan_if_needed(svc: Services, impact: ReplanImpact | None, trigger: str, background: BackgroundTasks) -> bool:
    if impact is not None and impact.trigger_run:
        svc.schedule_run(trigger, background)
        return True
    return False
