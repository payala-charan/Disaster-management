"""Pydantic v2 schemas: the API contract.

These mirror `frontend/src/types/index.ts` field-for-field (snake_case JSON). If a type changes,
change BOTH files; `tests/test_contract.py` fails when their field names drift apart.

Geometry is always exposed as {lat, lng} (or a list of them), never as raw PostGIS bytes.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class Contract(BaseModel):
    """Base for all API models. extra='forbid' turns a misspelled field into an error, not silent data loss."""

    model_config = ConfigDict(extra="forbid")


Provenance = Literal["known", "reported", "inferred"]


class GeoPoint(Contract):
    lat: float
    lng: float


# ---------------------------------------------------------------- incidents

IncidentType = Literal[
    "flood", "cyclone", "earthquake", "wildfire", "landslide", "industrial_accident", "urban_emergency",
]
IncidentSource = Literal["emergency_call", "field_team", "iot_sensor", "government_alert", "citizen_report"]
PriorityLevel = Literal["critical", "high", "moderate", "low", "verification_required"]
Confidence = Literal["high", "medium", "low"]
VerificationStatus = Literal["pending", "verified", "unverified"]
IncidentStatus = Literal["active", "monitoring", "resolved"]


class PriorityFactor(Contract):
    """One line of the transparent priority score."""

    name: str
    value: str
    points: int
    max_points: int
    provenance: Provenance


class PriorityAssessment(Contract):
    score: int  # 0-100, computed by deterministic rules, never by the LLM
    level: PriorityLevel
    factors: list[PriorityFactor]
    reasons: list[str]
    verification_needed: list[str]
    assessed_at: datetime


class Incident(Contract):
    id: str
    type: IncidentType
    title: str
    description: str
    zone: str
    location: GeoPoint | None  # None = location unknown (never guessed)
    reported_at: datetime
    source: IncidentSource
    people_affected: int | None  # None = not reported (never guessed)
    assistance_requested: list[str]
    infrastructure_issues: list[str]
    confidence: Confidence
    verification_status: VerificationStatus
    duplicate_of: str | None
    related_reports: list[str]
    supporting_info: str | None = None
    priority: PriorityAssessment | None
    assigned_resource_ids: list[str]
    status: IncidentStatus


# ---------------------------------------------------------------- resources, shelters, roads, weather

ResourceType = Literal["rescue_boat", "rescue_team", "ambulance", "emergency_vehicle", "medical_team"]
ResourceStatus = Literal["available", "reserved", "deployed", "returning", "unavailable"]


class Resource(Contract):
    id: str
    type: ResourceType
    name: str
    base_name: str
    location: GeoPoint
    status: ResourceStatus
    capacity: int
    capabilities: list[str]
    assigned_incident_id: str | None  # at most one incident: no double allocation
    expected_available_at: datetime | None


ShelterStatus = Literal["open", "full", "closed"]


class Shelter(Contract):
    """Available capacity is NOT a field: it is always computed as capacity - current_occupancy."""

    id: str
    name: str
    location: GeoPoint
    capacity: int
    current_occupancy: int
    status: ShelterStatus


RoadStatus = Literal["open", "partially_blocked", "closed", "unknown"]


class RoadSegment(Contract):
    id: str
    name: str
    serves_zone: str
    status: RoadStatus
    provenance: Provenance  # "known" closure vs "inferred" potential problem
    path: list[GeoPoint]
    updated_at: datetime
    note: str | None = None


class WeatherSnapshot(Contract):
    zone: str
    condition: str
    rainfall_mm_per_hr: float
    wind_kph: float
    temperature_c: float
    forecast_summary: str
    severe_warning: bool
    source: Literal["open-meteo", "simulation"]
    fetched_at: datetime


# ---------------------------------------------------------------- actions (human-in-the-loop)

ActionStatus = Literal["pending", "approved", "rejected", "in_progress", "completed", "superseded"]


class InputUsed(Contract):
    """One input a recommendation relied on, with where it came from (explainability)."""

    label: str
    value: str
    provenance: Provenance


class ResponseAction(Contract):
    id: str
    incident_id: str
    plan_id: str | None
    resource_ids: list[str]
    description: str
    priority: PriorityLevel
    status: ActionStatus
    rationale: list[str]
    assumptions: list[str]
    inputs_used: list[InputUsed]
    created_at: datetime
    approved_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    due_by: datetime | None
    remarks: list[str]


ActionEventType = Literal[
    "created", "approved", "rejected", "modified", "reassess_requested",
    "comment", "started", "completed", "superseded",
]


class ActionEvent(Contract):
    id: int
    action_id: str
    event: ActionEventType
    actor: str
    comment: str | None
    payload: dict[str, Any] | None
    at: datetime


# ---------------------------------------------------------------- alerts, shortages, changes

AlertSeverity = Literal["critical", "warning", "info"]
AlertKind = Literal[
    "critical_incident", "severity_increase", "resource_unavailable",
    "route_blocked", "shelter_capacity", "weather_worsening", "action_overdue",
]


class Alert(Contract):
    id: str
    kind: AlertKind
    severity: AlertSeverity
    message: str
    related_id: str | None
    created_at: datetime
    acknowledged: bool


class ResourceShortage(Contract):
    resource_type: ResourceType
    required: int
    available: int
    deficit: int  # required - available, computed deterministically
    incident_ids: list[str]
    note: str  # escalation suggestion, never an invented resource


class ConditionChange(Contract):
    id: str
    entity: str
    field: str
    old_value: str
    new_value: str
    changed_at: datetime
    provenance: Provenance


# ---------------------------------------------------------------- duplicates

class DuplicateBreakdown(Contract):
    """Every component of the duplicate score, so a reviewer can see why two reports were linked."""

    distance_m: float | None
    distance_score: float
    time_diff_min: float
    time_score: float
    type_match: float
    text_similarity: float


class DuplicateLink(Contract):
    id: str
    incident_id: str
    candidate_id: str
    score: float
    classification: Literal["potential_duplicate", "related"]
    breakdown: DuplicateBreakdown
    status: Literal["pending", "confirmed", "rejected"]
    reviewed_by: str | None
    reviewed_at: datetime | None
    created_at: datetime


# ---------------------------------------------------------------- planning & critic

class CriticFinding(Contract):
    check: str  # e.g. "double_allocation", "route_claim", "grounding"
    severity: Literal["blocking", "warning", "info"]
    message: str
    related_ids: list[str]


class AllocatedResource(Contract):
    resource_id: str
    resource_type: ResourceType
    reason: str
    travel_minutes: float | None
    travel_source: Literal["osrm", "estimated"] | None


class UnmetRequirement(Contract):
    resource_type: ResourceType
    required: int
    allocated: int
    shortfall: int


class PlanAllocation(Contract):
    incident_id: str
    priority: PriorityLevel
    resources: list[AllocatedResource]
    unmet: list[UnmetRequirement]
    verification_required: bool
    assumptions: list[str]


class Plan(Contract):
    id: str
    run_id: str
    variant: Literal["A", "B", "C"]
    title: str
    strategy: str
    allocations: list[PlanAllocation]
    shortages: list[ResourceShortage]
    explanation: str
    assumptions: list[str]
    critic_findings: list[CriticFinding]
    plan_score: float
    is_recommended: bool
    status: Literal["proposed", "superseded"]
    created_at: datetime


# ---------------------------------------------------------------- agent trace

AgentRunTrigger = Literal["manual", "new_report", "road_change", "weather_change", "resource_change", "reassess"]


class ToolCall(Contract):
    name: str
    args: dict[str, Any]
    result_summary: str
    ok: bool


class AgentStep(Contract):
    agent: str
    started_at: datetime
    duration_ms: int
    input_summary: str
    output_summary: str
    tool_calls: list[ToolCall]
    llm_used: bool
    error: str | None


class AgentRun(Contract):
    id: str
    trigger: AgentRunTrigger
    started_at: datetime
    finished_at: datetime | None
    status: Literal["running", "completed", "failed"]
    steps: list[AgentStep]


# ---------------------------------------------------------------- accessibility

class RouteInfo(Contract):
    road_id: str
    name: str
    status: RoadStatus
    provenance: Provenance
    note: str | None


class TravelEstimate(Contract):
    from_id: str  # resource ID or base name
    from_name: str
    distance_km: float
    duration_min: float
    source: Literal["osrm", "estimated"]


class AccessAnalysis(Contract):
    """Known routes and AI-inferred issues are kept in separate lists on purpose."""

    incident_id: str
    zone: str
    access_status: Literal["accessible", "partially_accessible", "inaccessible_by_road", "unknown"]
    known_routes: list[RouteInfo]
    inferred_issues: list[RouteInfo]
    travel_estimates: list[TravelEstimate]
    verification_needed: list[str]
    analyzed_at: datetime


# ---------------------------------------------------------------- situation snapshot & reports

class SituationSnapshot(Contract):
    incidents: list[Incident]
    resources: list[Resource]
    shelters: list[Shelter]
    roads: list[RoadSegment]
    weather: list[WeatherSnapshot]
    actions: list[ResponseAction]
    alerts: list[Alert]
    shortages: list[ResourceShortage]
    changes: list[ConditionChange]
    generated_at: datetime


class SituationReportMeta(Contract):
    id: str  # SR-001
    title: str
    generated_at: datetime
    run_id: str | None
    pdf_url: str


# ---------------------------------------------------------------- inputs

class IncidentCreate(Contract):
    """What an authorized user submits. The ID and assessment are generated by the system."""

    type: IncidentType
    title: str
    description: str
    zone: str
    location: GeoPoint
    reported_at: datetime
    source: IncidentSource
    people_affected: int | None
    assistance_requested: list[str]
    supporting_info: str | None = None


class SituationReportDetail(SituationReportMeta):
    narrative: str
    narrative_llm: bool  # False = deterministic template text


# ---------------------------------------------------------------- API request / response bodies

class IntakeReportIn(Contract):
    source: IncidentSource
    text: str | None = None
    payload: dict[str, Any] | None = None
    received_at: datetime | None = None


class IncidentPatch(Contract):
    verification_status: VerificationStatus | None = None
    status: IncidentStatus | None = None
    people_affected: int | None = None
    clear_people_affected: bool = False  # explicit "count unknown again" (None alone means "unchanged")


class ResourceStatusPatch(Contract):
    status: ResourceStatus
    reason: str
    expected_available_at: datetime | None = None


class ShelterPatch(Contract):
    current_occupancy: int | None = None
    status: ShelterStatus | None = None


class RoadPatch(Contract):
    status: RoadStatus
    provenance: Provenance = "known"
    note: str | None = None


class ActionDecision(Contract):
    comment: str | None = None


class ActionModify(Contract):
    resource_ids: list[str]
    comment: str | None = None


class WeatherInject(Contract):
    zone: str
    condition: str
    rainfall_mm_per_hr: float
    wind_kph: float
    temperature_c: float
    forecast_summary: str
    forecast_hourly: list[dict[str, float]]
    severe_warning: bool


class ResourceStatusChange(Contract):
    resource_id: str
    from_status: ResourceStatus | None
    to_status: ResourceStatus
    reason: str
    changed_at: datetime


class RunResult(Contract):
    run_id: str
    status: Literal["completed", "failed"]
    plan_ids: list[str]
    recommended_plan_id: str | None
    pending_actions: list[str]
    summary: str
    unresolved: list[str]
    errors: list[str]


class ZoneInfo(Contract):
    """A named area from the zone gazetteer (used for geocoding and the incident form)."""

    name: str
    center: GeoPoint
    description: str
