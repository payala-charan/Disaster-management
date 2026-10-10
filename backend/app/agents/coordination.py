"""Agent 7: Emergency Coordination & Action.

Consolidates the run into persisted results. NOTHING is dispatched and no resource is reserved here.

1. Choose the recommended plan: the highest plan_score among plans WITHOUT blocking critic findings
   (ties: A, then B, then C). If every plan has blocking findings, no actions are created.
2. Supersede the previous recommendation: earlier 'proposed' plans become 'superseded', and earlier
   'pending' actions become 'superseded' (with an audit event). Approved / in-progress actions are never
   touched: they are human decisions.
3. Persist plans A/B/C (with critic findings) and create one PENDING action per incident that the
   recommended plan allocates resources to. Each action carries rationale, assumptions, inputs used
   (with provenance) and a review deadline: critical 30 min, high / verification required 60, moderate 120, low 240.
4. Persist the alerts and condition changes collected by all agents.
5. Produce a situation summary and the list of unresolved issues (unallocated needs, verification items).
"""

from datetime import timedelta
from typing import Any

from sqlalchemy import select, update

from app.agents.base import Deps, StepRecorder, agent
from app.db import models as m
from app.db import repository as repo
from app.models.schemas import InputUsed, PriorityAssessment, ToolCall

DUE_MINUTES = {"critical": 30, "high": 60, "verification_required": 60, "moderate": 120, "low": 240}
VARIANT_ORDER = {"A": 0, "B": 1, "C": 2}


def choose_plan(plans: list[dict[str, Any]], findings: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    clean = [p for p in plans if not any(f["severity"] == "blocking" for f in findings.get(p["variant"], []))]
    return min(clean, key=lambda p: (-p["plan_score"], VARIANT_ORDER[p["variant"]]), default=None)


def inputs_used(prio: PriorityAssessment, access: dict[str, Any] | None) -> list[dict[str, Any]]:
    items = [InputUsed(label=f.name, value=f.value, provenance=f.provenance) for f in prio.factors]
    if access:
        items.append(InputUsed(label="Access status", value=access["access_status"].replace("_", " "),
                               provenance="known" if access["known_routes"] else "inferred"))
    return [i.model_dump() for i in items]


@agent("emergency_coordination")
def emergency_coordination(state: dict[str, Any], deps: Deps, rec: StepRecorder, db) -> dict[str, Any]:
    run_id, now = state["run_id"], deps.clock()
    plans, findings = state.get("plans", []), state.get("critic_findings", {})
    incidents = state.get("incidents", {})
    assessments = {k: PriorityAssessment(**v) for k, v in state.get("assessments", {}).items()}
    rec.input_summary = f"{len(plans)} plan(s) from run {run_id}; critic iterations {state.get('critic_iterations', 0)}"

    chosen = choose_plan(plans, findings)

    # 2. Supersede the previous recommendation (pending only: approved work is a human decision).
    stale_actions = db.scalars(select(m.Action).where(m.Action.status == "pending")).all()
    for action in stale_actions:
        action.status = "superseded"
        db.add(m.ActionEvent(action_id=action.id, event="superseded", actor="system", at=now,
                             comment=f"Replaced by the recommendation of {run_id}"))
    db.execute(update(m.Plan).where(m.Plan.status == "proposed").values(status="superseded"))

    # 3. Persist plans and pending actions.
    plan_ids: dict[str, str] = {}
    for p in plans:
        pid = repo.next_id(db, m.Plan, "PLAN")
        plan_ids[p["variant"]] = pid
        db.add(m.Plan(id=pid, run_id=run_id, variant=p["variant"], title=p["title"], strategy=p["strategy"],
                      allocations=p["allocations"], shortages=p["shortages"], explanation=p.get("explanation", ""),
                      assumptions=p["assumptions"], critic_findings=findings.get(p["variant"], []),
                      plan_score=p["plan_score"], is_recommended=chosen is not None and p["variant"] == chosen["variant"],
                      status="proposed", created_at=now))
    db.flush()

    pending, unresolved = [], []
    if chosen is None and plans:
        unresolved.append("No plan passed the reviewer's blocking checks: no actions were created. Manual planning required.")
    for alloc in (chosen or {}).get("allocations", []):
        iid = alloc["incident_id"]
        inc, prio = incidents[iid], assessments[iid]
        for u in alloc["unmet"]:
            unresolved.append(f"{iid}: {u['shortfall']} {u['resource_type']} still needed "
                              f"({u['allocated']}/{u['required']} allocated)")
        if not alloc["resources"]:
            unresolved.append(f"{iid}: no resources allocated. " + "; ".join(alloc["assumptions"][:2]))
            continue
        ids = [r["resource_id"] for r in alloc["resources"]]
        action = m.Action(
            id=repo.next_id(db, m.Action, "ACT"), incident_id=iid, plan_id=plan_ids[chosen["variant"]],
            resource_ids=ids, priority=prio.level, status="pending",
            description=f"Consider deploying {', '.join(ids)} to {iid} ({inc['zone']}): {inc['title']}",
            rationale=[f"Priority {prio.score}/100 ({prio.level.replace('_', ' ')})", *prio.reasons[:3],
                       *[r["reason"] for r in alloc["resources"]]],
            assumptions=alloc["assumptions"],
            inputs_used=inputs_used(prio, state.get("access_analysis", {}).get(iid)),
            created_at=now, due_by=now + timedelta(minutes=DUE_MINUTES[prio.level]),
        )
        db.add(action)
        db.flush()
        db.add(m.ActionEvent(action_id=action.id, event="created", actor="system", at=now,
                             comment=f"Recommended by {plan_ids[chosen['variant']]} ({chosen['title']}); awaiting coordinator approval"))
        pending.append(action.id)

    for a in assessments.values():
        unresolved.extend(a.verification_needed)
    unresolved = list(dict.fromkeys(unresolved))

    # 4. Persist alerts and condition changes from every agent.
    for alert in state.get("alerts_to_emit", []):
        db.add(m.Alert(id=repo.next_id(db, m.Alert, "ALT"), created_at=now, acknowledged=False, **alert))
    for change in state.get("changes", []):
        db.add(m.ConditionChange(id=repo.next_id(db, m.ConditionChange, "CHG"), changed_at=now, **change))

    # 5. Summary.
    critical = [i for i, a in assessments.items() if a.level == "critical"]
    shortages = "; ".join(f"{s['resource_type']} deficit {s['deficit']}" for s in (chosen or {}).get("shortages", []))
    summary = (f"{len(incidents)} active incident(s); critical: {', '.join(critical) or 'none'}. "
               + (f"Recommended {plan_ids[chosen['variant']]} ({chosen['title']}) with {len(pending)} action(s) "
                  f"pending coordinator approval." if chosen else "No recommendation passed review.")
               + (f" Shortages: {shortages}." if shortages else "")
               + f" {len(unresolved)} unresolved item(s).")
    rec.output_summary = summary
    rec.tool_calls.append(ToolCall(
        name="db.persist_recommendation", args={"plans": len(plans), "pending_actions": len(pending)}, ok=True,
        result_summary=(f"{len(plans)} plans, {len(pending)} pending actions, {len(stale_actions)} superseded, "
                        f"{len(state.get('alerts_to_emit', []))} alerts")))
    return {"plan_ids": list(plan_ids.values()),
            "recommended_plan_id": plan_ids[chosen["variant"]] if chosen else None,
            "pending_actions": pending, "summary": summary, "unresolved": unresolved}
