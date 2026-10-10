/** One function per backend endpoint (see backend/app/api). Types come from ../types (the shared contract). */
import { API_URL, http, patch, post } from "./client";
import type {
  AccessAnalysis, ActionEvent, AgentRun, Alert, DuplicateLink, Incident, IncidentCreate, IncidentPatch,
  IntakeReportIn, Plan, Resource, ResourceStatusChange, ResourceStatusPatch, ResponseAction, RoadPatch,
  RoadSegment, RunResult, Shelter, ShelterPatch, SituationReportDetail, SituationSnapshot, WeatherInject,
  WeatherSnapshot, ZoneInfo,
} from "../types";

export interface Health {
  status: string;
  postgis: string;
  llm: string;
  llm_model: string | null;
  weather_mode: string;
  routing_mode: string;
}
export interface ScenarioResult {
  test_case: string;
  title: string;
  applied: string[];
  run: RunResult;
}

const q = (id: string) => encodeURIComponent(id);

export const realApi = {
  health: () => http<Health>("/api/health"),
  getZones: () => http<ZoneInfo[]>("/api/zones"),
  getSituation: () => http<SituationSnapshot>("/api/situation"),

  // Incidents & intake
  createIncident: (input: IncidentCreate) => post<Incident>("/api/incidents", input),
  patchIncident: (id: string, body: IncidentPatch) => patch<Incident>(`/api/incidents/${q(id)}`, body),
  getDuplicates: (id: string) => http<DuplicateLink[]>(`/api/incidents/${q(id)}/duplicates`),
  reviewDuplicate: (linkId: string, decision: "confirm" | "reject") =>
    post<DuplicateLink>(`/api/duplicates/${q(linkId)}/${decision}`),
  submitReport: (body: IntakeReportIn) => post<{ raw_report_id: string }>("/api/intake/reports", body),

  // Agent runs & plans
  runAssessment: () => post<RunResult>("/api/assessments/run"),
  listRuns: (limit = 20) => http<AgentRun[]>(`/api/assessments?limit=${limit}`),
  getRun: (id: string) => http<AgentRun>(`/api/assessments/${q(id)}`),
  latestPlans: () => http<Plan[]>("/api/plans/latest"),

  // Actions (human approval)
  listActions: (status?: string) => http<ResponseAction[]>(`/api/actions${status ? `?status=${status}` : ""}`),
  actionEvents: (id: string) => http<ActionEvent[]>(`/api/actions/${q(id)}/events`),
  approve: (id: string, comment?: string) => post<ResponseAction>(`/api/actions/${q(id)}/approve`, { comment }),
  reject: (id: string, comment?: string) => post<ResponseAction>(`/api/actions/${q(id)}/reject`, { comment }),
  modify: (id: string, resource_ids: string[], comment?: string) =>
    post<ResponseAction>(`/api/actions/${q(id)}/modify`, { resource_ids, comment }),
  reassess: (id: string, comment?: string) => post<ResponseAction>(`/api/actions/${q(id)}/reassess`, { comment }),
  comment: (id: string, comment: string) => post<ResponseAction>(`/api/actions/${q(id)}/comment`, { comment }),
  start: (id: string, comment?: string) => post<ResponseAction>(`/api/actions/${q(id)}/start`, { comment }),
  complete: (id: string, comment?: string) => post<ResponseAction>(`/api/actions/${q(id)}/complete`, { comment }),

  // Resources, shelters, roads, weather
  getResources: () => http<Resource[]>("/api/resources"),
  setResourceStatus: (id: string, body: ResourceStatusPatch) => patch<Resource>(`/api/resources/${q(id)}/status`, body),
  resourceHistory: (id: string) => http<ResourceStatusChange[]>(`/api/resources/${q(id)}/history`),
  getShelters: () => http<Shelter[]>("/api/shelters"),
  patchShelter: (id: string, body: ShelterPatch) => patch<Shelter>(`/api/shelters/${q(id)}`, body),
  getRoads: () => http<RoadSegment[]>("/api/roads"),
  patchRoad: (id: string, body: RoadPatch) => patch<RoadSegment>(`/api/roads/${q(id)}`, body),
  getAccess: (incidentId: string) => http<AccessAnalysis>(`/api/access/${q(incidentId)}`),
  getWeather: () => http<WeatherSnapshot[]>("/api/weather"),
  refreshWeather: () => post<WeatherSnapshot[]>("/api/weather/refresh"),

  // Alerts & reports
  listAlerts: (acknowledged?: boolean) =>
    http<Alert[]>(`/api/alerts${acknowledged === undefined ? "" : `?acknowledged=${acknowledged}`}`),
  ackAlert: (id: string) => post<Alert>(`/api/alerts/${q(id)}/ack`),
  generateReport: () => post<SituationReportDetail>("/api/reports/situation"),
  listReports: () => http<SituationReportDetail[]>("/api/reports"),
  reportPdfUrl: (r: SituationReportDetail) => `${API_URL}${r.pdf_url}`,

  // Simulation (live demo)
  simReset: () => post<{ status: string }>("/api/simulation/reset"),
  simReport: (body: IntakeReportIn) => post<{ raw_report_id: string }>("/api/simulation/report", body),
  simRoad: (road_id: string, body: RoadPatch) => post<unknown>("/api/simulation/road", { road_id, ...body }),
  simWeather: (body: WeatherInject) => post<{ worsened: boolean }>("/api/simulation/weather", body),
  simShelter: (shelter_id: string, body: ShelterPatch) => post<unknown>("/api/simulation/shelter", { shelter_id, ...body }),
  simResource: (resource_id: string, body: ResourceStatusPatch) =>
    post<unknown>("/api/simulation/resource", { resource_id, ...body }),
  simScenarios: () => http<Record<string, { title: string; scenario: string | null }>>("/api/simulation/scenarios"),
  simScenario: (tc: string) => post<ScenarioResult>(`/api/simulation/scenario/${q(tc)}`),
};

export type Api = typeof realApi;
