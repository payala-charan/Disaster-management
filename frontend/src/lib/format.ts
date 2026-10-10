import type {
  ActionStatus, AlertSeverity, PriorityLevel, Provenance, ResourceStatus, ResourceType, RoadStatus, Shelter,
} from "../types";

export const PRIORITY_STYLE: Record<PriorityLevel, { label: string; cls: string }> = {
  critical: { label: "Critical", cls: "bg-red-600/20 text-red-300 ring-red-500/40" },
  high: { label: "High", cls: "bg-orange-600/20 text-orange-300 ring-orange-500/40" },
  moderate: { label: "Moderate", cls: "bg-yellow-600/20 text-yellow-200 ring-yellow-500/40" },
  low: { label: "Low", cls: "bg-emerald-600/20 text-emerald-300 ring-emerald-500/40" },
  verification_required: { label: "Verification Required", cls: "bg-violet-600/20 text-violet-300 ring-violet-500/40" },
};

export const PROVENANCE_STYLE: Record<Provenance, { label: string; cls: string }> = {
  known: { label: "Known", cls: "bg-sky-600/20 text-sky-300 border border-sky-500/40" },
  reported: { label: "Reported", cls: "bg-slate-600/30 text-slate-300 border border-slate-500/40" },
  inferred: { label: "AI-Inferred", cls: "bg-fuchsia-600/15 text-fuchsia-300 border border-dashed border-fuchsia-400/60" },
};

export const ROAD_STYLE: Record<RoadStatus, { label: string; cls: string }> = {
  open: { label: "Open", cls: "text-emerald-300" },
  partially_blocked: { label: "Partially blocked", cls: "text-amber-300" },
  closed: { label: "Closed", cls: "text-red-300" },
  unknown: { label: "Unknown: verification required", cls: "text-violet-300" },
};

export const RESOURCE_LABEL: Record<ResourceType, string> = {
  rescue_boat: "Rescue Boats",
  rescue_team: "Rescue Teams",
  ambulance: "Ambulances",
  emergency_vehicle: "Emergency Vehicles",
  medical_team: "Medical Teams",
};

/** Deterministic: never stored, always derived. */
export const shelterAvailable = (s: Shelter) => Math.max(0, s.capacity - s.current_occupancy);
export const shelterUtilization = (s: Shelter) => (s.capacity ? s.current_occupancy / s.capacity : 0);

export function timeAgo(iso: string): string {
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60_000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  return `${Math.floor(min / 60)} h ${min % 60} min ago`;
}

export const pretty = (s: string) => s.replace(/_/g, " ");

export const ACTION_STATUS_STYLE: Record<ActionStatus, { label: string; cls: string }> = {
  pending: { label: "Pending approval", cls: "bg-amber-600/20 text-amber-200 ring-amber-500/40" },
  approved: { label: "Approved", cls: "bg-sky-600/20 text-sky-200 ring-sky-500/40" },
  in_progress: { label: "In progress", cls: "bg-blue-600/20 text-blue-200 ring-blue-500/40" },
  completed: { label: "Completed", cls: "bg-emerald-600/20 text-emerald-200 ring-emerald-500/40" },
  rejected: { label: "Rejected", cls: "bg-slate-600/30 text-slate-300 ring-slate-500/40" },
  superseded: { label: "Superseded", cls: "bg-slate-700/30 text-slate-400 ring-slate-600/40 line-through" },
};

export const RESOURCE_STATUS_STYLE: Record<ResourceStatus, { label: string; cls: string }> = {
  available: { label: "Available", cls: "bg-emerald-600/20 text-emerald-200 ring-emerald-500/40" },
  reserved: { label: "Reserved", cls: "bg-amber-600/20 text-amber-200 ring-amber-500/40" },
  deployed: { label: "Deployed", cls: "bg-blue-600/20 text-blue-200 ring-blue-500/40" },
  returning: { label: "Returning", cls: "bg-violet-600/20 text-violet-200 ring-violet-500/40" },
  unavailable: { label: "Unavailable", cls: "bg-slate-700/40 text-slate-400 ring-slate-600/40" },
};

export const SEVERITY_STYLE: Record<AlertSeverity, { label: string; border: string; text: string }> = {
  critical: { label: "Critical", border: "border-red-500", text: "text-red-300" },
  warning: { label: "Warning", border: "border-amber-500", text: "text-amber-300" },
  info: { label: "Info", border: "border-sky-500", text: "text-sky-300" },
};

/** Manual resource status changes the backend accepts (reserve/deploy/return happen via actions). */
export const MANUAL_TRANSITIONS: Record<ResourceStatus, ResourceStatus[]> = {
  available: ["unavailable"],
  reserved: ["unavailable"],
  deployed: ["unavailable"],
  returning: ["available", "unavailable"],
  unavailable: ["available"],
};

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "–";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function minutesUntil(iso: string): number {
  return Math.round((new Date(iso).getTime() - Date.now()) / 60_000);
}
