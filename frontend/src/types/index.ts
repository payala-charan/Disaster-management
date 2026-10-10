// ============================================================
// Data contract shared with the FastAPI backend (snake_case).
// Every Pydantic model in the backend will mirror these types.
// ============================================================

/** Where a fact came from. Known facts and AI inferences must never look equal. */
export type Provenance = "known" | "reported" | "inferred";

export interface GeoPoint {
  lat: number;
  lng: number;
}

// ---------- Incidents ----------
export type IncidentType =
  | "flood" | "cyclone" | "earthquake" | "wildfire"
  | "landslide" | "industrial_accident" | "urban_emergency";

export type IncidentSource =
  | "emergency_call" | "field_team" | "iot_sensor"
  | "government_alert" | "citizen_report";

export type PriorityLevel =
  | "critical" | "high" | "moderate" | "low" | "verification_required";

export type Confidence = "high" | "medium" | "low";
export type VerificationStatus = "pending" | "verified" | "unverified";
export type IncidentStatus = "active" | "monitoring" | "resolved";

/** One line of the transparent priority score. */
export interface PriorityFactor {
  name: string;            // e.g. "Access route"
  value: string;           // e.g. "Main road closed"
  points: number;          // points awarded by the deterministic rule
  max_points: number;      // maximum possible for this factor
  provenance: Provenance;  // is the input known, reported or inferred?
}

export interface PriorityAssessment {
  score: number;                  // 0–100, computed by rules, never by the LLM
  level: PriorityLevel;
  factors: PriorityFactor[];
  reasons: string[];              // human-readable explanation
  verification_needed: string[];  // missing or uncertain information
  assessed_at: string;            // ISO timestamp
}

export interface Incident {
  id: string;                         // INC-001
  type: IncidentType;
  title: string;
  description: string;
  zone: string;
  location: GeoPoint | null;          // null = location unknown (never guessed)
  reported_at: string;
  source: IncidentSource;
  people_affected: number | null;     // null = not reported (never guessed)
  assistance_requested: string[];
  infrastructure_issues: string[];
  confidence: Confidence;
  verification_status: VerificationStatus;
  duplicate_of: string | null;        // linked for human review, never deleted
  related_reports: string[];
  supporting_info?: string | null;
  priority: PriorityAssessment | null;
  assigned_resource_ids: string[];
  status: IncidentStatus;
}

// ---------- Resources ----------
export type ResourceType =
  | "rescue_boat" | "rescue_team" | "ambulance"
  | "emergency_vehicle" | "medical_team";

/** Lifecycle: available → reserved → deployed → returning → available */
export type ResourceStatus =
  | "available" | "reserved" | "deployed" | "returning" | "unavailable";

export interface Resource {
  id: string;                            // BOAT-001
  type: ResourceType;
  name: string;
  base_name: string;                     // e.g. "Response Center 1"
  location: GeoPoint;
  status: ResourceStatus;
  capacity: number;                      // people it can carry or treat
  capabilities: string[];                // e.g. ["water_rescue"]
  assigned_incident_id: string | null;   // one incident at most: no double allocation
  expected_available_at: string | null;
}

// ---------- Shelters ----------
export type ShelterStatus = "open" | "full" | "closed";

export interface Shelter {
  id: string;
  name: string;
  location: GeoPoint;
  capacity: number;
  current_occupancy: number;
  status: ShelterStatus;
  // available capacity is NOT stored: always computed as capacity - current_occupancy
}

// ---------- Roads & accessibility ----------
export type RoadStatus = "open" | "partially_blocked" | "closed" | "unknown";

export interface RoadSegment {
  id: string;
  name: string;
  serves_zone: string;
  status: RoadStatus;
  provenance: Provenance;   // "known" closure vs "inferred" potential problem
  path: GeoPoint[];
  updated_at: string;
  note?: string | null;
}

// ---------- Weather ----------
export interface WeatherSnapshot {
  zone: string;
  condition: string;
  rainfall_mm_per_hr: number;
  wind_kph: number;
  temperature_c: number;
  forecast_summary: string;
  severe_warning: boolean;
  source: "open-meteo" | "simulation";
  fetched_at: string;
}

// ---------- Response actions (human-in-the-loop) ----------
export type ActionStatus =
  | "pending" | "approved" | "rejected" | "in_progress" | "completed"
  | "superseded";             // replaced by a newer plan after conditions changed

/** One input a recommendation relied on, with where it came from. */
export interface InputUsed {
  label: string;              // e.g. "People affected"
  value: string;              // e.g. "40"
  provenance: Provenance;
}

export interface ResponseAction {
  id: string;                 // ACT-001
  incident_id: string;
  plan_id: string | null;     // plan that produced it (null = registered manually)
  resource_ids: string[];
  description: string;
  priority: PriorityLevel;
  status: ActionStatus;
  rationale: string[];        // why the system recommends this
  assumptions: string[];      // what it assumed, shown to the approver
  inputs_used: InputUsed[];   // the facts it relied on, with provenance
  created_at: string;
  approved_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  due_by: string | null;      // overdue → alert
  remarks: string[];
}

export type ActionEventType =
  | "created" | "approved" | "rejected" | "modified" | "reassess_requested"
  | "comment" | "started" | "completed" | "superseded";

/** Audit trail entry: who did what to an action, and when. */
export interface ActionEvent {
  id: number;
  action_id: string;
  event: ActionEventType;
  actor: string;
  comment: string | null;
  payload: Record<string, unknown> | null;
  at: string;
}

// ---------- Alerts ----------
export type AlertSeverity = "critical" | "warning" | "info";
export type AlertKind =
  | "critical_incident" | "severity_increase" | "resource_unavailable"
  | "route_blocked" | "shelter_capacity" | "weather_worsening" | "action_overdue";

export interface Alert {
  id: string;
  kind: AlertKind;
  severity: AlertSeverity;
  message: string;
  related_id: string | null;
  created_at: string;
  acknowledged: boolean;
}

// ---------- Shortages & changes ----------
export interface ResourceShortage {
  resource_type: ResourceType;
  required: number;
  available: number;
  deficit: number;           // required - available, computed deterministically
  incident_ids: string[];
  note: string;              // escalation suggestion, never an invented resource
}

export interface ConditionChange {
  id: string;
  entity: string;            // e.g. "Main Road to Zone C"
  field: string;             // e.g. "status"
  old_value: string;
  new_value: string;
  changed_at: string;
  provenance: Provenance;
}

// ---------- Duplicates (linked for human review, never deleted) ----------
export interface DuplicateBreakdown {
  distance_m: number | null;
  distance_score: number;     // 0–1
  time_diff_min: number;
  time_score: number;         // 0–1
  type_match: number;         // 0 or 1
  text_similarity: number;    // 0–1
}

export interface DuplicateLink {
  id: string;                 // DUP-001
  incident_id: string;        // the newer report
  candidate_id: string;       // the existing incident it may duplicate
  score: number;              // weighted sum of the breakdown
  classification: "potential_duplicate" | "related";
  breakdown: DuplicateBreakdown;
  status: "pending" | "confirmed" | "rejected";
  reviewed_by: string | null;
  reviewed_at: string | null;
  created_at: string;
}

// ---------- Planning & reviewer ----------
export interface CriticFinding {
  check: string;              // e.g. "double_allocation", "route_claim", "grounding"
  severity: "blocking" | "warning" | "info";
  message: string;
  related_ids: string[];
}

export interface AllocatedResource {
  resource_id: string;
  resource_type: ResourceType;
  reason: string;             // why this resource was chosen
  travel_minutes: number | null;
  travel_source: "osrm" | "estimated" | null;
}

export interface UnmetRequirement {
  resource_type: ResourceType;
  required: number;
  allocated: number;
  shortfall: number;
}

export interface PlanAllocation {
  incident_id: string;
  priority: PriorityLevel;
  resources: AllocatedResource[];
  unmet: UnmetRequirement[];
  verification_required: boolean;
  assumptions: string[];
}

export interface Plan {
  id: string;                 // PLAN-001
  run_id: string;
  variant: "A" | "B" | "C";   // Priority-first | Coverage | Verified-access only
  title: string;
  strategy: string;
  allocations: PlanAllocation[];
  shortages: ResourceShortage[];
  explanation: string;
  assumptions: string[];
  critic_findings: CriticFinding[];
  plan_score: number;         // deterministic, used to pick the recommended plan
  is_recommended: boolean;
  status: "proposed" | "superseded";
  created_at: string;
}

// ---------- Agent trace ----------
export type AgentRunTrigger =
  | "manual" | "new_report" | "road_change" | "weather_change" | "resource_change" | "reassess";

export interface ToolCall {
  name: string;
  args: Record<string, unknown>;
  result_summary: string;
  ok: boolean;
}

export interface AgentStep {
  agent: string;
  started_at: string;
  duration_ms: number;
  input_summary: string;
  output_summary: string;
  tool_calls: ToolCall[];
  llm_used: boolean;
  error: string | null;
}

export interface AgentRun {
  id: string;                 // RUN-001
  trigger: AgentRunTrigger;
  started_at: string;
  finished_at: string | null;
  status: "running" | "completed" | "failed";
  steps: AgentStep[];
}

// ---------- Accessibility (known routes and AI inferences kept apart) ----------
export interface RouteInfo {
  road_id: string;
  name: string;
  status: RoadStatus;
  provenance: Provenance;
  note: string | null;
}

export interface TravelEstimate {
  from_id: string;
  from_name: string;
  distance_km: number;
  duration_min: number;
  source: "osrm" | "estimated";
}

export interface AccessAnalysis {
  incident_id: string;
  zone: string;
  access_status: "accessible" | "partially_accessible" | "inaccessible_by_road" | "unknown";
  known_routes: RouteInfo[];
  inferred_issues: RouteInfo[];
  travel_estimates: TravelEstimate[];
  verification_needed: string[];
  analyzed_at: string;
}

// ---------- Situation reports ----------
export interface SituationReportMeta {
  id: string;                 // SR-001
  title: string;
  generated_at: string;
  run_id: string | null;
  pdf_url: string;
}

// ---------- One snapshot of the whole operational picture ----------
export interface SituationSnapshot {
  incidents: Incident[];
  resources: Resource[];
  shelters: Shelter[];
  roads: RoadSegment[];
  weather: WeatherSnapshot[];
  actions: ResponseAction[];
  alerts: Alert[];
  shortages: ResourceShortage[];
  changes: ConditionChange[];
  generated_at: string;
}

// ---------- Inputs ----------
/** What an authorized user submits. The ID and assessment are generated by the system. */
export interface IncidentCreate {
  type: IncidentType;
  title: string;
  description: string;
  zone: string;
  location: GeoPoint;
  reported_at: string;
  source: IncidentSource;
  people_affected: number | null;
  assistance_requested: string[];
  supporting_info?: string | null;
}

export interface SituationReportDetail extends SituationReportMeta {
  narrative: string;
  narrative_llm: boolean;     // false = deterministic template text
}

// ---------- API request / response bodies ----------
export interface IntakeReportIn {
  source: IncidentSource;
  text?: string | null;
  payload?: Record<string, unknown> | null;
  received_at?: string | null;
}

export interface IncidentPatch {
  verification_status?: VerificationStatus | null;
  status?: IncidentStatus | null;
  people_affected?: number | null;
  clear_people_affected?: boolean;
}

export interface ResourceStatusPatch {
  status: ResourceStatus;
  reason: string;
  expected_available_at?: string | null;
}

export interface ShelterPatch {
  current_occupancy?: number | null;
  status?: ShelterStatus | null;
}

export interface RoadPatch {
  status: RoadStatus;
  provenance?: Provenance;
  note?: string | null;
}

export interface ActionDecision {
  comment?: string | null;
}

export interface ActionModify {
  resource_ids: string[];
  comment?: string | null;
}

export interface WeatherInject {
  zone: string;
  condition: string;
  rainfall_mm_per_hr: number;
  wind_kph: number;
  temperature_c: number;
  forecast_summary: string;
  forecast_hourly: { hour: number; precipitation_mm: number; wind_kph: number }[];
  severe_warning: boolean;
}

export interface ResourceStatusChange {
  resource_id: string;
  from_status: ResourceStatus | null;
  to_status: ResourceStatus;
  reason: string;
  changed_at: string;
}

export interface RunResult {
  run_id: string;
  status: "completed" | "failed";
  plan_ids: string[];
  recommended_plan_id: string | null;
  pending_actions: string[];
  summary: string;
  unresolved: string[];
  errors: string[];
}

/** A named area from the backend zone gazetteer. */
export interface ZoneInfo {
  name: string;
  center: GeoPoint;
  description: string;
}
