"""Agent 6: Resource Allocation & Response Planning.

Builds the allocation inputs from the other agents' outputs and calls the deterministic allocation
engine, which returns plans A/B/C with shortages and a recommended plan. The LLM then writes the
recommended plan's explanation FROM THE PLAN JSON ONLY (grounding-checked). The other two plans get the
template text, which saves cost and latency.

On a critic loop-back, resources the critic found invalid (e.g. no longer available in the database)
are excluded before re-planning.
"""

from typing import Any

from app.agents.base import Deps, StepRecorder, agent
from app.engines.access import ZoneAccess, assess_zone_access
from app.engines.allocation import AllocationInput, TravelInfo, generate_plans
from app.engines.models import IncidentFacts, ResourceFacts
from app.engines.resources import RequirementEstimate
from app.graph.state import travel_key
from app.integrations.llm import template_explanation
from app.models.schemas import PriorityAssessment


def allocation_inputs(state: dict[str, Any]) -> list[AllocationInput]:
    access = {z: ZoneAccess(**a) for z, a in state.get("zone_access", {}).items()}
    resources = [ResourceFacts(**r) for r in state.get("resources", [])]
    inputs = []
    for iid, data in state.get("incidents", {}).items():
        if iid not in state.get("assessments", {}):
            continue  # severity failed for it: nothing to plan on
        inc = IncidentFacts(**data)
        inputs.append(AllocationInput(
            incident=inc, priority=PriorityAssessment(**state["assessments"][iid]),
            requirements=RequirementEstimate(**state["requirements"][iid]),
            access=access.get(inc.zone) or assess_zone_access(inc.zone, []),
            assigned=[r for r in resources if r.assigned_incident_id == iid],
        ))
    return inputs


@agent("resource_allocation")
def resource_allocation(state: dict[str, Any], deps: Deps, rec: StepRecorder, db) -> dict[str, Any]:
    excluded = set(state.get("excluded_resource_ids", []))
    inputs = allocation_inputs(state)
    resources = [ResourceFacts(**r) for r in state.get("resources", []) if r["id"] not in excluded]
    travel = {}
    for key, value in state.get("travel", {}).items():
        rid, iid = key.split("|")
        travel[(rid, iid)] = TravelInfo(**value)
    iteration = state.get("critic_iterations", 0)
    rec.input_summary = (f"{len(inputs)} assessed incident(s), {sum(r.status == 'available' for r in resources)} "
                         f"available resource(s); iteration {iteration + 1}"
                         + (f"; excluded by critic: {sorted(excluded)}" if excluded else ""))

    plans = rec.tool("allocation.generate_plans", {"incidents": len(inputs), "excluded": sorted(excluded)},
                     lambda: generate_plans(inputs, resources, travel),
                     lambda ps: "; ".join(f"{p.variant} score {p.plan_score}{' (recommended)' if p.is_recommended else ''}"
                                          for p in ps))
    out = []
    for plan in plans:
        data = plan.model_dump(mode="json")
        if plan.is_recommended:
            result = deps.llm.explain(data, "plan_explanation")
            rec.llm("llm.explain_plan", {"variant": plan.variant}, result,
                    "grounded" if result.grounded else f"rejected by grounding check: {result.grounding_problems}")
            data.update(explanation=result.text, explanation_llm=result.llm_used, explanation_grounded=result.grounded)
        else:
            data.update(explanation=template_explanation(data, "plan_explanation"), explanation_llm=False,
                        explanation_grounded=True)
        out.append(data)

    best = next(p for p in plans if p.is_recommended)
    shortages = ", ".join(f"{s.resource_type} -{s.deficit}" for s in best.shortages) or "none"
    rec.output_summary = f"Plans A/B/C generated; recommended {best.variant} (score {best.plan_score}); shortages: {shortages}"
    return {"plans": out}
