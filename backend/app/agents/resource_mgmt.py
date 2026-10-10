"""Agent 5: Emergency Resource Management.

Reports the registry as it is now: every resource with its status and current assignment (from active
reserved/deployed assignments), counts by type and status, expected availability of returning or
unavailable units, and shelter capacity state (engines/shelters.py).
"""

from typing import Any

from app.agents.base import Deps, StepRecorder, agent
from app.db import repository as repo
from app.engines.shelters import shelter_state


@agent("resource_management")
def resource_management(state: dict[str, Any], deps: Deps, rec: StepRecorder, db) -> dict[str, Any]:
    resources = rec.tool("resource_registry.list", {}, lambda: repo.resource_facts(db),
                         lambda r: f"{len(r)} resources")
    shelters = rec.tool("shelter_registry.list", {}, lambda: repo.shelters(db), lambda s: f"{len(s)} shelters")
    rec.input_summary = f"{len(resources)} resources, {len(shelters)} shelters"

    summary: dict[str, dict[str, int]] = {}
    for r in resources:
        summary.setdefault(r.type, {}).setdefault(r.status, 0)
        summary[r.type][r.status] += 1
    states = [shelter_state(s, deps.settings.shelter_alert_threshold) for s in shelters]

    available = {t: c.get("available", 0) for t, c in summary.items()}
    pressured = [s.shelter_id for s in states if s.approaching_threshold]
    rec.output_summary = (f"Available: {', '.join(f'{t} {n}' for t, n in sorted(available.items()))}; "
                          f"shelters at/above threshold: {pressured or 'none'}")
    return {
        "resources": [r.model_dump(mode="json") for r in resources],
        "resource_summary": summary,
        "shelter_status": [s.model_dump(mode="json") for s in states],
    }
