"""Shared workflow state: the 'blackboard' agents communicate through.

Agents never call each other. Each node reads the keys it needs and returns only the keys it changed;
LangGraph merges them. Values are plain JSON (model_dump(mode="json")) so checkpoints are portable
and readable; agents rebuild Pydantic models when they need them.

Keys written by parallel nodes (trace, alerts_to_emit, changes, errors) use `operator.add` as a reducer
so concurrent updates are appended instead of overwriting each other.

| key | written by | content |
|---|---|---|
| run_id, trigger, new_report_ids | runner | run identity and what started it |
| new_incident_ids, incidents, duplicate_candidates | intake | normalized incidents (IncidentFacts), proposed duplicate links |
| weather | weather | zone -> WeatherFacts |
| zone_access, access_analysis, travel, shelter_options, isolated_zones | geospatial | access per zone/incident, travel matrix, nearest shelters |
| resources, resource_summary, shelter_status | resource_mgmt | registry state with assignments |
| requirements, assessments | severity | RequirementEstimate and PriorityAssessment per incident |
| plans, excluded_resource_ids | allocation | three PlanDrafts (+ explanation) |
| critic_findings, critic_iterations, blocking | critic | findings per plan variant |
| plan_ids, recommended_plan_id, pending_actions, summary, unresolved | coordination | persisted results |
| alerts_to_emit, changes, errors, trace | any (appended) | persisted by coordination / runner |
"""

import operator
from typing import Annotated, Any, TypedDict

JSON = dict[str, Any]


class WorkflowState(TypedDict, total=False):
    run_id: str
    trigger: str
    new_report_ids: list[str]

    new_incident_ids: list[str]
    incidents: dict[str, JSON]
    duplicate_candidates: list[JSON]

    weather: dict[str, JSON]

    zone_access: dict[str, JSON]
    access_analysis: dict[str, JSON]
    travel: dict[str, JSON]          # "RESOURCE_ID|INCIDENT_ID" -> TravelInfo
    shelter_options: dict[str, list[JSON]]
    isolated_zones: list[str]

    resources: list[JSON]
    resource_summary: dict[str, dict[str, int]]
    shelter_status: list[JSON]

    requirements: dict[str, JSON]
    assessments: dict[str, JSON]

    plans: list[JSON]
    excluded_resource_ids: list[str]

    critic_findings: dict[str, list[JSON]]  # plan variant -> findings
    critic_iterations: int
    blocking: bool

    plan_ids: list[str]
    recommended_plan_id: str | None
    pending_actions: list[str]
    summary: str
    unresolved: list[str]

    alerts_to_emit: Annotated[list[JSON], operator.add]
    changes: Annotated[list[JSON], operator.add]
    errors: Annotated[list[str], operator.add]
    trace: Annotated[list[JSON], operator.add]


def travel_key(resource_id: str, incident_id: str) -> str:
    return f"{resource_id}|{incident_id}"
