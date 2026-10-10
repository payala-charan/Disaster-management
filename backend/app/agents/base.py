"""Shared machinery for the eight agents.

- `Deps`: everything an agent may use (DB session factory, LLM, weather, routing, geocoder, settings, clock).
  It is passed to the graph as LangGraph *context*: it is available to every node but never stored in
  checkpoints, so the API key and DB connections never end up in workflow state.
- `StepRecorder`: records each tool call (name, args, result summary, ok) and whether the LLM was used.
- `@agent(name)`: wraps an agent function. It opens its own DB session (parallel nodes run in threads
  and SQLAlchemy sessions are not thread-safe), times the work, commits, and writes one `agent_steps`
  row. If the agent raises, its DB work is rolled back, the error is recorded, and the workflow
  continues with what it has (graceful degradation).
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from langgraph.runtime import Runtime
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db import models as m
from app.integrations.geocoding import Geocoder
from app.integrations.llm import LLMClient, LLMMeta
from app.integrations.routing import RoutingService
from app.integrations.weather import WeatherService
from app.models.schemas import AgentStep, ToolCall


@dataclass
class Deps:
    session_factory: Callable[[], Session]
    llm: LLMClient
    weather: WeatherService
    routing: RoutingService
    geocoder: Geocoder
    settings: Settings
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))


def build_deps(session_factory: Callable[[], Session] | None = None, settings: Settings | None = None,
               llm: LLMClient | None = None) -> Deps:
    from app.db.repository import DbWeatherSource
    from app.db.session import SessionLocal

    settings = settings or get_settings()
    session_factory = session_factory or SessionLocal
    return Deps(
        session_factory=session_factory,
        llm=llm or LLMClient(settings),
        weather=WeatherService(settings, simulation=DbWeatherSource(session_factory)),
        routing=RoutingService(settings),
        geocoder=Geocoder(settings),
        settings=settings,
    )


def _short(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


class StepRecorder:
    def __init__(self) -> None:
        self.tool_calls: list[ToolCall] = []
        self.llm_used = False
        self.input_summary = ""
        self.output_summary = ""

    def tool(self, name: str, args: dict[str, Any], fn: Callable[[], Any],
             summarize: Callable[[Any], str] = _short) -> Any:
        """Run fn as a named tool call and record it (failures are recorded, then re-raised)."""
        try:
            result = fn()
        except Exception as exc:
            self.tool_calls.append(ToolCall(name=name, args=args, result_summary=f"{type(exc).__name__}", ok=False))
            raise
        self.tool_calls.append(ToolCall(name=name, args=args, result_summary=_short(summarize(result)), ok=True))
        return result

    def llm(self, name: str, args: dict[str, Any], meta: LLMMeta, summary: str) -> None:
        """Record an LLM call. ok=False means the deterministic fallback was used."""
        self.llm_used = self.llm_used or meta.llm_used
        note = summary if meta.llm_used else f"fallback ({meta.error}): {summary}"
        self.tool_calls.append(ToolCall(name=name, args=args, result_summary=_short(note), ok=meta.llm_used))


AgentFn = Callable[[dict[str, Any], Deps, StepRecorder, Session], dict[str, Any]]


def agent(name: str) -> Callable[[AgentFn], Callable[..., dict[str, Any]]]:
    def decorate(fn: AgentFn):
        def node(state: dict[str, Any], runtime: Runtime[Deps]) -> dict[str, Any]:
            deps = runtime.context
            rec = StepRecorder()
            started, t0 = deps.clock(), time.perf_counter()
            error = None
            with deps.session_factory() as db:
                try:
                    update = fn(state, deps, rec, db)
                    db.commit()
                except Exception as exc:  # noqa: BLE001 - record and continue
                    db.rollback()
                    error = f"{type(exc).__name__}: {exc}"
                    update = {"errors": [f"{name}: {error}"]}
            step = AgentStep(
                agent=name, started_at=started, duration_ms=int((time.perf_counter() - t0) * 1000),
                input_summary=rec.input_summary, output_summary=rec.output_summary if not error else f"FAILED: {error}",
                tool_calls=rec.tool_calls, llm_used=rec.llm_used, error=error,
            )
            with deps.session_factory() as db:
                db.add(m.AgentStep(run_id=state["run_id"], **step.model_dump(mode="json", exclude={"started_at"}),
                                   started_at=started))
                db.commit()
            return {**update, "trace": [step.model_dump(mode="json")]}

        # Copy name/doc only. functools.wraps would set __wrapped__, and LangGraph would then inspect the
        # inner signature and stop injecting `runtime`.
        node.__name__, node.__doc__ = name, fn.__doc__
        return node

    return decorate
