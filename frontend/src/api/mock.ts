/** Offline mock (VITE_USE_MOCKS=true): the original demo data, read-only except incident registration. */
import * as mock from "../mocks/data";
import type { Incident, IncidentCreate, SituationSnapshot, ZoneInfo } from "../types";
import type { Api } from "./real";
import { realApi } from "./real";

/** Simulates network latency and returns a deep copy (like real JSON from an API). */
const respond = <T,>(data: T, ms = 250): Promise<T> =>
  new Promise((resolve) => setTimeout(() => resolve(structuredClone(data)), ms));

const unavailable = Object.fromEntries(
  Object.keys(realApi).map((k) => [k, () => Promise.reject(new Error("Not available in mock mode (VITE_USE_MOCKS=true)"))]),
) as unknown as Api;

export const mockApi: Api = {
  ...unavailable,
  reportPdfUrl: () => "#",
  getZones: () =>
    respond<ZoneInfo[]>(Object.entries(mock.ZONES).map(([name, center]) => ({ name, center, description: "" }))),
  getSituation: () =>
    respond<SituationSnapshot>({
      incidents: mock.incidents, resources: mock.resources, shelters: mock.shelters, roads: mock.roads,
      weather: mock.weather, actions: mock.actions, alerts: mock.alerts, shortages: mock.shortages,
      changes: mock.changes, generated_at: new Date().toISOString(),
    }),
  createIncident: (input: IncidentCreate): Promise<Incident> => {
    const maxNum = Math.max(0, ...mock.incidents.map((i) => Number(i.id.split("-")[1]) || 0));
    const incident: Incident = {
      ...input, id: `INC-${String(maxNum + 1).padStart(3, "0")}`, infrastructure_issues: [], confidence: "medium",
      verification_status: "pending", duplicate_of: null, related_reports: [], priority: null,
      assigned_resource_ids: [], status: "active",
    };
    mock.incidents.push(incident);
    return respond(incident);
  },
};
