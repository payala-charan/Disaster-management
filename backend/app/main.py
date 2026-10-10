"""FastAPI application.

Run:  uvicorn app.main:app --reload --port 8000      (interactive API docs at /docs)

create_app() is an application factory: tests build an app bound to the test database with a fake LLM,
production uses the defaults from Settings. Everything stateful (DB sessions, agent dependencies,
WebSocket bus) lives on app.state.services.
"""

import asyncio
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.agents.base import build_deps
from app.api import incidents, operations, simulation, situation
from app.core.config import Settings, get_settings
from app.integrations.llm import LLMClient
from app.services.context import Services
from app.services.errors import ServiceError
from app.services.events import EventBus
from app.services.overdue import check_overdue

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, session_factory: Callable[[], Session] | None = None,
               llm: LLMClient | None = None, background_checks: bool = True) -> FastAPI:
    settings = settings or get_settings()
    deps = build_deps(session_factory=session_factory, settings=settings, llm=llm)
    services = Services(settings=settings, session_factory=deps.session_factory, deps=deps, bus=EventBus())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        services.bus.attach_loop(asyncio.get_running_loop())
        task = asyncio.create_task(_overdue_loop(services)) if background_checks else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan,
                  description="Decision-support API. AI agents recommend; only coordinators approve. "
                              "Nothing is dispatched automatically.")
    app.state.services = services

    # Registered BEFORE CORSMiddleware, so it sits inside it: an unexpected error becomes a JSON 500 that
    # still carries CORS headers. (Otherwise the browser hides the response and reports "Failed to fetch".)
    @app.middleware("http")
    async def unexpected_errors(request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:  # noqa: BLE001
            log.exception("Unhandled error on %s %s", request.method, request.url.path)
            return JSONResponse(status_code=500, content={"detail": f"Internal server error ({type(exc).__name__}); see server log"})

    # CORS origins come from CORS_ORIGINS in .env ("*" = any origin). allow_credentials stays False: browsers
    # reject "*" together with credentials, and the frontend sends no cookies (identity is the X-User header).
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                       allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    for router in (situation.router, incidents.router, operations.router, simulation.router):
        app.include_router(router, prefix="/api")

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await services.bus.connect(websocket)
        try:
            while True:
                await websocket.receive_text()  # clients only listen; this keeps the socket open
        except WebSocketDisconnect:
            services.bus.disconnect(websocket)

    return app


async def _overdue_loop(services: Services) -> None:
    while True:
        await asyncio.sleep(services.settings.overdue_check_s)
        try:
            await asyncio.to_thread(check_overdue, services)
        except Exception:  # noqa: BLE001 - a failed check must not stop future checks
            log.exception("overdue check failed")


app = create_app()
