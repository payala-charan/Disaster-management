"""LangGraph orchestration of the eight agents.

    START -> incident_intake
          -> [weather_intelligence | geospatial_accessibility | resource_management]   (parallel fan-out)
          -> situation_assessment                                                      (join)
          -> resource_allocation -> reviewer_critic
          -> (blocking findings and attempts left ? resource_allocation : emergency_coordination)
          -> END  (pending actions now wait for a coordinator)

Why severity runs after the fan-out: the priority score uses weather (escalation factor), road access
(access factor) and current assignments (response gap). Running it after all three have finished is
the simplest ordering where every score uses this run's data, with no second severity pass.

Human approval is NOT a graph node: the run ends with `pending` actions in the database, and the
coordinator approves them later through the API (Phase 5). That approval can happen hours later, from
another process, so persisted actions are the hand-off point rather than a paused graph.

Checkpointing: an in-memory checkpointer keeps every step's state for inspection during the process
lifetime. Each invocation gets a UNIQUE thread_id ("RUN-001:<uuid>"): run IDs restart after a database
reset, and reusing a thread would resume the old checkpoint and merge stale state into the new run. In production, use `langgraph-checkpoint-postgres` so that
state survives restarts.

CLI: python -m app.graph.workflow --mermaid docs/agent_graph.md
"""

import argparse
import threading
import uuid
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.agents.base import Deps, build_deps
from app.agents.coordination import emergency_coordination
from app.agents.critic import reviewer_critic, route_after_critic
from app.agents.geospatial import geospatial_accessibility
from app.agents.intake import incident_intake
from app.agents.planning import resource_allocation
from app.agents.resource_mgmt import resource_management
from app.agents.severity import situation_assessment
from app.agents.weather_intel import weather_intelligence
from app.db import models as m
from app.db import repository as repo
from app.graph.state import WorkflowState

PARALLEL = ["weather_intelligence", "geospatial_accessibility", "resource_management"]


def build_graph() -> StateGraph:
    g = StateGraph(WorkflowState, context_schema=Deps)
    for name, fn in [
        ("incident_intake", incident_intake),
        ("weather_intelligence", weather_intelligence),
        ("geospatial_accessibility", geospatial_accessibility),
        ("resource_management", resource_management),
        ("situation_assessment", situation_assessment),
        ("resource_allocation", resource_allocation),
        ("reviewer_critic", reviewer_critic),
        ("emergency_coordination", emergency_coordination),
    ]:
        g.add_node(name, fn)
    g.add_edge(START, "incident_intake")
    for name in PARALLEL:
        g.add_edge("incident_intake", name)
    g.add_edge(PARALLEL, "situation_assessment")  # waits for all three
    g.add_edge("situation_assessment", "resource_allocation")
    g.add_edge("resource_allocation", "reviewer_critic")
    g.add_conditional_edges("reviewer_critic", route_after_critic,
                            {"resource_allocation": "resource_allocation", "emergency_coordination": "emergency_coordination"})
    g.add_edge("emergency_coordination", END)
    return g


CHECKPOINTER = InMemorySaver()
GRAPH = build_graph().compile(checkpointer=CHECKPOINTER)
_RUN_LOCK = threading.Lock()  # one run at a time: runs supersede each other's pending actions


def run_assessment(trigger: str = "manual", deps: Deps | None = None) -> dict[str, Any]:
    """Create an agent_runs row, execute the graph, and close the run. Never raises."""
    deps = deps or build_deps()
    with _RUN_LOCK:
        with deps.session_factory() as db:
            run_id = repo.next_id(db, m.AgentRun, "RUN")
            db.add(m.AgentRun(id=run_id, trigger=trigger, started_at=deps.clock(), status="running"))
            db.commit()
        status, final = "completed", {}
        thread_id = f"{run_id}:{uuid.uuid4().hex}"
        try:
            final = GRAPH.invoke({"run_id": run_id, "trigger": trigger, "critic_iterations": 0},
                                 {"configurable": {"thread_id": thread_id}}, context=deps)
        except Exception as exc:  # noqa: BLE001 - a failed run is recorded, not raised to the API
            status, final = "failed", {"errors": [f"workflow: {type(exc).__name__}: {exc}"]}
        with deps.session_factory() as db:
            run = db.get(m.AgentRun, run_id)
            run.status, run.finished_at = status, deps.clock()
            db.commit()
    return {
        "run_id": run_id, "thread_id": thread_id, "status": status,
        "plan_ids": final.get("plan_ids", []), "recommended_plan_id": final.get("recommended_plan_id"),
        "pending_actions": final.get("pending_actions", []), "summary": final.get("summary", ""),
        "unresolved": final.get("unresolved", []), "errors": final.get("errors", []),
        "agents": [s["agent"] for s in final.get("trace", [])],
    }


def mermaid() -> str:
    return GRAPH.get_graph().draw_mermaid()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Agent workflow utilities")
    parser.add_argument("--mermaid", metavar="PATH", help="write the graph as a Mermaid diagram (Markdown)")
    parser.add_argument("--run", action="store_true", help="run one assessment against the configured database")
    args = parser.parse_args()
    if args.mermaid:
        with open(args.mermaid, "w") as fh:
            fh.write("# Agent workflow (generated by `python -m app.graph.workflow --mermaid`)\n\n```mermaid\n"
                     + mermaid() + "```\n")
        print(f"Wrote {args.mermaid}")
    if args.run:
        import json

        print(json.dumps(run_assessment("manual"), indent=2))
