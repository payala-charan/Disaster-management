import type {
  Alert, ConditionChange, GeoPoint, Incident, PriorityFactor, Provenance,
  Resource, ResourceShortage, ResourceType, ResponseAction, RoadSegment,
  Shelter, WeatherSnapshot,
} from "../types";

// ---------- Time helpers (relative to "now" so the demo always looks live) ----------
const now = Date.now();
const ago = (min: number) => new Date(now - min * 60_000).toISOString();
const ahead = (min: number) => new Date(now + min * 60_000).toISOString();

// ---------- Geography: "Example District" ----------
export const ZONES: Record<string, GeoPoint> = {
  "Zone A": { lat: 13.0108, lng: 80.227 },
  "Zone B": { lat: 13.035, lng: 80.209 },
  "Zone C": { lat: 12.979, lng: 80.181 },
};
const RC1: GeoPoint = { lat: 13.04, lng: 80.25 };   // Response Center 1
const RC2: GeoPoint = { lat: 12.995, lng: 80.255 }; // Response Center 2

const f = (
  name: string, value: string, points: number, max_points: number, provenance: Provenance,
): PriorityFactor => ({ name, value, points, max_points, provenance });

// ---------- Incidents ----------
export const incidents: Incident[] = [
  {
    id: "INC-001", type: "flood", title: "Residential flooding",
    description: "Flood water has entered approximately 20 houses in Zone A. Around 40 residents may require evacuation. The main access road is partially flooded.",
    zone: "Zone A", location: ZONES["Zone A"], reported_at: ago(95), source: "emergency_call",
    people_affected: 40, assistance_requested: ["evacuation"],
    infrastructure_issues: ["road_partially_flooded"], confidence: "medium",
    verification_status: "verified", duplicate_of: null, related_reports: [],
    assigned_resource_ids: ["BOAT-001", "TEAM-001"], status: "active",
    priority: {
      score: 53, level: "moderate", assessed_at: ago(10),
      factors: [
        f("Threat to life", "Residential flooding, no injuries reported", 12, 25, "reported"),
        f("People affected", "40 reported", 14, 20, "reported"),
        f("Access route", "Main road partially blocked", 8, 15, "known"),
        f("Weather / escalation", "Heavy rain continuing 6 h", 13, 15, "known"),
        f("Infrastructure impact", "Road partially flooded", 6, 10, "reported"),
        f("Response gap", "BOAT-001 and TEAM-001 assigned", 0, 15, "known"),
      ],
      reasons: ["Response resources already assigned", "Rainfall may raise water levels"],
      verification_needed: [],
    },
  },
  {
    id: "INC-002", type: "flood", title: "People stranded",
    description: "12 people stranded on rooftops after water levels rose quickly near the canal.",
    zone: "Zone B", location: ZONES["Zone B"], reported_at: ago(60), source: "field_team",
    people_affected: 12, assistance_requested: ["water_rescue"],
    infrastructure_issues: [], confidence: "high", verification_status: "verified",
    duplicate_of: null, related_reports: ["INC-004"], assigned_resource_ids: [], status: "active",
    priority: {
      score: 62, level: "high", assessed_at: ago(10),
      factors: [
        f("Threat to life", "People stranded by rising water", 22, 25, "reported"),
        f("People affected", "12 reported", 8, 20, "reported"),
        f("Access route", "Main road open", 0, 15, "known"),
        f("Weather / escalation", "Heavy rain continuing 6 h", 13, 15, "known"),
        f("Infrastructure impact", "None reported", 4, 10, "reported"),
        f("Response gap", "No resources assigned", 15, 15, "known"),
      ],
      reasons: ["Immediate threat to stranded people", "No response assigned yet"],
      verification_needed: ["Confirm water conditions permit boat operation"],
    },
  },
  {
    id: "INC-003", type: "flood", title: "Village access cut off",
    description: "Village in Zone C cut off by flood water. Around 75 residents potentially affected.",
    zone: "Zone C", location: ZONES["Zone C"], reported_at: ago(40), source: "government_alert",
    people_affected: 75, assistance_requested: ["evacuation", "medical_check"],
    infrastructure_issues: ["main_road_closed"], confidence: "medium",
    verification_status: "pending", duplicate_of: null, related_reports: [],
    assigned_resource_ids: [], status: "active",
    priority: {
      score: 89, level: "critical", assessed_at: ago(5),
      factors: [
        f("Threat to life", "Community isolated by flood water", 18, 25, "reported"),
        f("People affected", "75 potentially affected", 20, 20, "reported"),
        f("Access route", "Main road closed", 15, 15, "known"),
        f("Weather / escalation", "Heavy rain continuing 6 h", 13, 15, "known"),
        f("Infrastructure impact", "Main road closed", 8, 10, "known"),
        f("Response gap", "No resources assigned", 15, 15, "known"),
      ],
      reasons: [
        "Large number of potentially affected people",
        "Access route unavailable",
        "Weather conditions may worsen",
        "No response resources currently assigned",
        "Alternative access method may be required",
      ],
      verification_needed: ["Confirm number of people affected", "Alternative route verification required"],
    },
  },
  {
    id: "INC-004", type: "flood", title: "Citizen report: people on roofs",
    description: "Saw maybe 10-15 people on roofs near the canal in Zone B, water rising.",
    zone: "Zone B", location: { lat: 13.0355, lng: 80.2096 }, reported_at: ago(55),
    source: "citizen_report", people_affected: null, assistance_requested: ["water_rescue"],
    infrastructure_issues: [], confidence: "low", verification_status: "unverified",
    duplicate_of: "INC-002", related_reports: ["INC-002"], assigned_resource_ids: [],
    status: "active", priority: null,
  },
];

// ---------- Resources ----------
function fleet(
  prefix: string, type: ResourceType, label: string, count: number,
  base: string, at: GeoPoint, capacity: number, capabilities: string[],
): Resource[] {
  return Array.from({ length: count }, (_, i) => ({
    id: `${prefix}-${String(i + 1).padStart(3, "0")}`,
    type, name: `${label} ${i + 1}`, base_name: base,
    location: { lat: at.lat + i * 0.0008, lng: at.lng + i * 0.0008 },
    status: "available", capacity, capabilities,
    assigned_incident_id: null, expected_available_at: null,
  }));
}

const overrides: Record<string, Partial<Resource>> = {
  "BOAT-001": { status: "deployed", assigned_incident_id: "INC-001", location: ZONES["Zone A"] },
  "TEAM-001": { status: "deployed", assigned_incident_id: "INC-001", location: ZONES["Zone A"] },
  "AMB-006": { status: "returning", expected_available_at: ahead(30) },
  "VEH-008": { status: "unavailable" },
};

export const resources: Resource[] = [
  ...fleet("BOAT", "rescue_boat", "Rescue Boat", 5, "Response Center 1", RC1, 8, ["water_rescue"]),
  ...fleet("TEAM", "rescue_team", "Rescue Team", 4, "Response Center 1", RC1, 6, ["evacuation", "water_rescue"]),
  ...fleet("AMB", "ambulance", "Ambulance", 6, "Response Center 2", RC2, 2, ["medical_transport"]),
  ...fleet("VEH", "emergency_vehicle", "Emergency Vehicle", 8, "Response Center 2", RC2, 10, ["road_transport"]),
].map((r) => ({ ...r, ...overrides[r.id] }));

// ---------- Shelters ----------
export const shelters: Shelter[] = [
  { id: "SHL-A", name: "Shelter A", location: { lat: 13.02, lng: 80.235 }, capacity: 200, current_occupancy: 160, status: "open" },
  { id: "SHL-B", name: "Shelter B", location: { lat: 13.045, lng: 80.22 }, capacity: 150, current_occupancy: 60, status: "open" },
  { id: "SHL-C", name: "Shelter C", location: { lat: 13.005, lng: 80.205 }, capacity: 300, current_occupancy: 110, status: "open" },
];

// ---------- Roads ----------
export const roads: RoadSegment[] = [
  {
    id: "RD-A", name: "Main Road to Zone A", serves_zone: "Zone A", status: "partially_blocked",
    provenance: "known", path: [RC1, { lat: 13.025, lng: 80.24 }, ZONES["Zone A"]], updated_at: ago(50),
  },
  {
    id: "RD-B", name: "Main Road to Zone B", serves_zone: "Zone B", status: "open",
    provenance: "known", path: [RC1, { lat: 13.04, lng: 80.23 }, ZONES["Zone B"]], updated_at: ago(45),
  },
  {
    id: "RD-C", name: "Main Road to Zone C", serves_zone: "Zone C", status: "closed",
    provenance: "known", path: [RC2, { lat: 12.988, lng: 80.215 }, ZONES["Zone C"]], updated_at: ago(25),
    note: "Closed due to flooding (traffic authority report)",
  },
  {
    id: "RD-C2", name: "Riverside Link Road", serves_zone: "Zone C", status: "unknown",
    provenance: "inferred", path: [{ lat: 13.005, lng: 80.205 }, { lat: 12.992, lng: 80.192 }, ZONES["Zone C"]],
    updated_at: ago(15),
    note: "Potential accessibility problem: low-lying road near river during heavy rain. Route verification required.",
  },
];

// ---------- Weather (simulation mode) ----------
export const weather: WeatherSnapshot[] = ["Zone A", "Zone B", "Zone C"].map((zone) => ({
  zone, condition: "Heavy rain", rainfall_mm_per_hr: zone === "Zone C" ? 24 : 18,
  wind_kph: 32, temperature_c: 26, forecast_summary: "Continued heavy rainfall for next 6 hours",
  severe_warning: true, source: "simulation", fetched_at: ago(8),
}));

// ---------- Actions ----------
export const actions: ResponseAction[] = [
  {
    id: "ACT-001", incident_id: "INC-001", plan_id: null, resource_ids: ["BOAT-001", "TEAM-001"],
    description: "Evacuate residents from flooded houses in Zone A", priority: "moderate",
    status: "in_progress", rationale: ["40 residents may require evacuation"],
    assumptions: [], inputs_used: [{ label: "People affected", value: "40", provenance: "reported" }],
    created_at: ago(80), approved_at: ago(75), started_at: ago(70), completed_at: null, due_by: null, remarks: [],
  },
  {
    id: "ACT-002", incident_id: "INC-002", plan_id: "PLAN-001", resource_ids: ["BOAT-002", "BOAT-003"],
    description: "Consider deploying two rescue boats to Zone B for stranded people",
    priority: "high", status: "pending",
    rationale: ["12 people stranded", "Two boats with capacity 8 each cover 12 people", "Main road to Zone B open"],
    assumptions: ["Water and route conditions permit boat deployment: verification required"],
    inputs_used: [
      { label: "People stranded", value: "12", provenance: "reported" },
      { label: "Main Road to Zone B", value: "open", provenance: "known" },
    ],
    created_at: ago(6), approved_at: null, started_at: null, completed_at: null, due_by: null, remarks: [],
  },
];

// ---------- Alerts ----------
export const alerts: Alert[] = [
  { id: "ALT-001", kind: "critical_incident", severity: "critical", message: "INC-003 assessed as Critical: village access cut off, 75 people potentially affected", related_id: "INC-003", created_at: ago(5), acknowledged: false },
  { id: "ALT-002", kind: "route_blocked", severity: "critical", message: "Main Road to Zone C changed to CLOSED", related_id: "RD-C", created_at: ago(25), acknowledged: false },
  { id: "ALT-003", kind: "weather_worsening", severity: "warning", message: "Heavy rainfall expected to continue for 6 hours", related_id: null, created_at: ago(8), acknowledged: false },
  { id: "ALT-004", kind: "shelter_capacity", severity: "info", message: "Shelter A at 80% occupancy (alert threshold 85%)", related_id: "SHL-A", created_at: ago(20), acknowledged: true },
];

// ---------- Shortages ----------
export const shortages: ResourceShortage[] = [
  {
    resource_type: "rescue_boat", required: 8, available: 4, deficit: 4,
    incident_ids: ["INC-002", "INC-003"],
    note: "Escalate request to district or state emergency operations for additional water-rescue capacity.",
  },
];

// ---------- Recent changes ----------
export const changes: ConditionChange[] = [
  { id: "CHG-001", entity: "Main Road to Zone C", field: "status", old_value: "partially_blocked", new_value: "closed", changed_at: ago(25), provenance: "known" },
  { id: "CHG-002", entity: "INC-003", field: "people_affected", old_value: "40", new_value: "75", changed_at: ago(12), provenance: "reported" },
  { id: "CHG-003", entity: "Weather forecast", field: "rainfall_outlook", old_value: "moderate", new_value: "heavy (6 h)", changed_at: ago(8), provenance: "known" },
];
