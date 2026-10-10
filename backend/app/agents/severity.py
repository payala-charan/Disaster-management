"""Agent 2: Situation Assessment & Severity.

Runs AFTER the weather, geospatial and resource agents (the graph joins them first), so every score
uses this run's weather, access and assignment data. It only calls deterministic engines:
estimate_requirements -> assess_priority. The LLM never sets a score.

History: a new priority_assessments row is stored only when score, level or verification items changed,
so the history shows real changes. Alerts:
- critical_incident when an incident becomes critical,
- severity_increase when its level rises (low < moderate < high < critical).
"""

from typing import Any

from app.agents.base import Deps, StepRecorder, agent
from app.db import models as m
from app.db import repository as repo
from app.engines.access import ZoneAccess, assess_zone_access
from app.engines.models import IncidentFacts, ResourceFacts, WeatherFacts
from app.engines.priority import assess_priority
from app.engines.resources import estimate_requirements

LEVEL_RANK = {"low": 0, "moderate": 1, "high": 2, "critical": 3}


@agent("situation_assessment")
def situation_assessment(state: dict[str, Any], deps: Deps, rec: StepRecorder, db) -> dict[str, Any]:
    incidents = [IncidentFacts(**v) for v in state.get("incidents", {}).values()]
    weather = {z: WeatherFacts(**w) for z, w in state.get("weather", {}).items()}
    access = {z: ZoneAccess(**a) for z, a in state.get("zone_access", {}).items()}
    resources = [ResourceFacts(**r) for r in state.get("resources", [])] or repo.resource_facts(db)
    rec.input_summary = (f"{len(incidents)} incident(s); weather for {len(weather)} zone(s); "
                         f"access for {len(access)} zone(s)")

    requirements, assessments, alerts = {}, {}, []
    now = deps.clock()
    for inc in incidents:
        zone_access = access.get(inc.zone) or assess_zone_access(inc.zone, [])  # missing -> unknown, flagged
        assigned = [r for r in resources if r.assigned_incident_id == inc.id]
        req = estimate_requirements(inc, zone_access)
        prio = rec.tool("priority.assess", {"incident": inc.id},
                        lambda: assess_priority(inc, zone_access, weather.get(inc.zone), req, assigned, now),
                        lambda p: f"{p.score} {p.level}")

        prev = repo.latest_priority(db, inc.id)
        if prev is None or (prev.score, prev.level, prev.verification_needed) != (prio.score, prio.level,
                                                                                  prio.verification_needed):
            db.add(m.PriorityAssessmentRow(
                incident_id=inc.id, run_id=state["run_id"], score=prio.score, level=prio.level,
                factors=[f.model_dump() for f in prio.factors], reasons=prio.reasons,
                verification_needed=prio.verification_needed, assessed_at=prio.assessed_at,
            ))
        if prio.level == "critical" and (prev is None or prev.level != "critical"):
            alerts.append({"kind": "critical_incident", "severity": "critical", "related_id": inc.id,
                           "message": f"{inc.id} assessed as Critical ({prio.score}): {inc.title} in {inc.zone}"})
        elif prev and LEVEL_RANK.get(prio.level, -1) > LEVEL_RANK.get(prev.level, 99):
            alerts.append({"kind": "severity_increase", "severity": "warning", "related_id": inc.id,
                           "message": f"{inc.id} priority rose from {prev.level} ({prev.score}) "
                                      f"to {prio.level} ({prio.score})"})
        requirements[inc.id] = req.model_dump(mode="json")
        assessments[inc.id] = prio.model_dump(mode="json")

    ranked = sorted(assessments.items(), key=lambda kv: -kv[1]["score"])
    rec.output_summary = "; ".join(f"{i} {a['score']} {a['level']}" for i, a in ranked) or "no incidents"
    return {"requirements": requirements, "assessments": assessments, "alerts_to_emit": alerts}
