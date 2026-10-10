import { useState } from "react";
import { Link } from "react-router-dom";
import L from "leaflet";
import {
  CircleMarker, MapContainer, Marker, Polyline, TileLayer, Tooltip,
} from "react-leaflet";
import { api } from "../api";
import { useApi } from "../api/useApi";
import { Panel, PriorityBadge, ProvenanceBadge } from "../components/ui";
import {
  RESOURCE_LABEL, ROAD_STYLE, shelterAvailable, shelterUtilization, timeAgo,
} from "../lib/format";
import type {
  Incident, PriorityLevel, Resource, ResourceStatus, ResourceType,
  RoadSegment, RoadStatus, Shelter, SituationSnapshot,
} from "../types";

// ---------- Visual encodings ----------
const PRIORITY_COLOR: Record<PriorityLevel, string> = {
  critical: "#ef4444", high: "#f97316", moderate: "#eab308",
  low: "#10b981", verification_required: "#a78bfa",
};
const ROAD_COLOR: Record<RoadStatus, string> = {
  open: "#34d399", partially_blocked: "#fbbf24", closed: "#f87171", unknown: "#a78bfa",
};
const RES_COLOR: Record<ResourceStatus, string> = {
  available: "#34d399", reserved: "#fbbf24", deployed: "#60a5fa",
  returning: "#a78bfa", unavailable: "#64748b",
};
const RES_LETTER: Record<ResourceType, string> = {
  rescue_boat: "B", rescue_team: "T", ambulance: "A", emergency_vehicle: "V", medical_team: "M",
};

function badgeIcon(letter: string, color: string, round = false) {
  return L.divIcon({
    className: "",
    iconSize: [22, 22],
    iconAnchor: [11, 11],
    html: `<div style="width:22px;height:22px;border-radius:${round ? "50%" : "5px"};
      background:#0f172a;border:2px solid ${color};color:${color};
      font:600 11px system-ui;display:flex;align-items:center;justify-content:center">${letter}</div>`,
  });
}

const shelterColor = (s: Shelter) => {
  const u = shelterUtilization(s);
  return s.status === "closed" ? "#64748b" : u >= 0.85 ? "#f87171" : u >= 0.7 ? "#fbbf24" : "#38bdf8";
};

type Selection = { kind: "incident" | "resource" | "shelter" | "road"; id: string } | null;
type Layers = { incidents: boolean; resources: boolean; shelters: boolean; roads: boolean };

// ---------- Page ----------
export default function LiveMap() {
  const { data, error, loading } = useApi(api.getSituation);
  const [selected, setSelected] = useState<Selection>(null);
  const [layers, setLayers] = useState<Layers>({ incidents: true, resources: true, shelters: true, roads: true });

  if (error) return <p className="text-red-400">Failed to load map data: {error}</p>;
  if (loading || !data) return <p className="text-slate-400">Loading map…</p>;

  const pick = (kind: NonNullable<Selection>["kind"], id: string) => ({ click: () => setSelected({ kind, id }) });

  return (
    <div className="flex h-[calc(100vh-7rem)] gap-4">
      {/* Map */}
      <div className="relative min-w-0 flex-1 overflow-hidden rounded-xl border border-slate-800">
        <MapContainer center={[13.01, 80.22]} zoom={13} className="h-full w-full" style={{ background: "#0b1120" }}>
          <TileLayer className="dark-tiles"
            url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          />

          {layers.roads && data.roads.map((r) => (
            <Polyline
              key={r.id}
              positions={r.path.map((p) => [p.lat, p.lng])}
              pathOptions={{
                color: ROAD_COLOR[r.status],
                weight: 5,
                opacity: 0.9,
                dashArray: r.provenance === "inferred" ? "6 10" : undefined,  // inferred ≠ known
              }}
              eventHandlers={pick("road", r.id)}
            >
              <Tooltip sticky>{r.name}: {ROAD_STYLE[r.status].label}</Tooltip>
            </Polyline>
          ))}

          {layers.incidents && data.incidents.map((i) => {
            if (!i.location) return null; // unknown location: listed elsewhere, never drawn at a guessed point
            const isDup = Boolean(i.duplicate_of);
            const color = i.priority ? PRIORITY_COLOR[i.priority.level] : "#94a3b8";
            const radius = isDup ? 7 : 10 + Math.sqrt(i.people_affected ?? 9) * 1.6;
            return (
              <CircleMarker
                key={i.id}
                center={[i.location.lat, i.location.lng]}
                radius={radius}
                pathOptions={{
                  color, fillColor: color, fillOpacity: isDup ? 0.15 : 0.35,
                  weight: 2, dashArray: isDup ? "3 4" : undefined,
                }}
                eventHandlers={pick("incident", i.id)}
              >
                <Tooltip>{i.id} · {i.title}{isDup ? ` (possible duplicate of ${i.duplicate_of})` : ""}</Tooltip>
              </CircleMarker>
            );
          })}

          {layers.shelters && data.shelters.map((s) => (
            <Marker key={s.id} position={[s.location.lat, s.location.lng]}
              icon={badgeIcon("S", shelterColor(s), true)} eventHandlers={pick("shelter", s.id)}>
              <Tooltip>{s.name}: {shelterAvailable(s)} spaces free</Tooltip>
            </Marker>
          ))}

          {layers.resources && data.resources.map((r) => (
            <Marker key={r.id} position={[r.location.lat, r.location.lng]}
              icon={badgeIcon(RES_LETTER[r.type], RES_COLOR[r.status])} eventHandlers={pick("resource", r.id)}>
              <Tooltip>{r.id} · {r.status}</Tooltip>
            </Marker>
          ))}
        </MapContainer>

        {/* Layer toggles (above the map panes) */}
        <div className="absolute right-3 top-3 z-[1000] rounded-lg border border-slate-700 bg-slate-900/90 p-3 text-sm">
          {(Object.keys(layers) as (keyof Layers)[]).map((k) => (
            <label key={k} className="flex cursor-pointer items-center gap-2 capitalize">
              <input type="checkbox" checked={layers[k]} onChange={() => setLayers({ ...layers, [k]: !layers[k] })} />
              {k}
            </label>
          ))}
        </div>
      </div>

      {/* Side panel */}
      <div className="w-80 shrink-0 space-y-4 overflow-y-auto">
        <Panel title={selected ? "Selected item" : "Map details"}>
          {selected ? <Details sel={selected} data={data} /> : (
            <p className="text-sm text-slate-400">Click an incident, resource, shelter or road to view its current status.</p>
          )}
        </Panel>
        <Legend />
      </div>
    </div>
  );
}

// ---------- Detail views ----------
function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3 py-1 text-sm">
      <span className="text-slate-400">{label}</span>
      <span className="text-right">{children}</span>
    </div>
  );
}

function Details({ sel, data }: { sel: NonNullable<Selection>; data: SituationSnapshot }) {
  if (sel.kind === "incident") {
    const i = data.incidents.find((x) => x.id === sel.id);
    return i ? <IncidentDetails i={i} /> : null;
  }
  if (sel.kind === "resource") {
    const r = data.resources.find((x) => x.id === sel.id);
    return r ? <ResourceDetails r={r} /> : null;
  }
  if (sel.kind === "shelter") {
    const s = data.shelters.find((x) => x.id === sel.id);
    return s ? <ShelterDetails s={s} /> : null;
  }
  const road = data.roads.find((x) => x.id === sel.id);
  return road ? <RoadDetails r={road} /> : null;
}

function IncidentDetails({ i }: { i: Incident }) {
  return (
    <div>
      <p className="font-mono text-xs text-slate-400">{i.id}</p>
      <p className="font-medium">{i.title} · {i.zone}</p>
      <div className="mt-2 flex items-center gap-2">
        <PriorityBadge level={i.priority?.level} />
        {i.priority && <span className="text-sm text-slate-300">score {i.priority.score}/100</span>}
      </div>
      {i.duplicate_of && (
        <p className="mt-2 rounded border border-violet-700/50 bg-violet-950/40 p-2 text-xs text-violet-200">
          Possible duplicate of {i.duplicate_of}. Linked for human review, not deleted.
        </p>
      )}
      <div className="mt-3 divide-y divide-slate-800">
        <Row label="People affected">{i.people_affected ?? "Unknown: verification required"}</Row>
        <Row label="Source">{i.source.replace(/_/g, " ")}</Row>
        <Row label="Confidence">{i.confidence}</Row>
        <Row label="Verification">{i.verification_status}</Row>
        <Row label="Assigned">{i.assigned_resource_ids.join(", ") || "None"}</Row>
        <Row label="Reported">{timeAgo(i.reported_at)}</Row>
      </div>
      <Link to={`/incidents/${i.id}`} className="mt-3 inline-block text-sm text-sky-400 hover:underline">
        Open full assessment →
      </Link>
    </div>
  );
}

function ResourceDetails({ r }: { r: Resource }) {
  return (
    <div>
      <p className="font-mono text-xs text-slate-400">{r.id}</p>
      <p className="font-medium">{r.name}</p>
      <div className="mt-3 divide-y divide-slate-800">
        <Row label="Type">{RESOURCE_LABEL[r.type]}</Row>
        <Row label="Status"><span style={{ color: RES_COLOR[r.status] }}>{r.status}</span></Row>
        <Row label="Base">{r.base_name}</Row>
        <Row label="Capacity">{r.capacity} people</Row>
        <Row label="Capabilities">{r.capabilities.join(", ")}</Row>
        <Row label="Assignment">{r.assigned_incident_id ?? "None"}</Row>
        {r.expected_available_at && <Row label="Expected available">{new Date(r.expected_available_at).toLocaleTimeString()}</Row>}
      </div>
    </div>
  );
}

function ShelterDetails({ s }: { s: Shelter }) {
  const pct = Math.round(shelterUtilization(s) * 100);
  return (
    <div>
      <p className="font-medium">{s.name}</p>
      <div className="mt-3 divide-y divide-slate-800">
        <Row label="Status">{s.status}</Row>
        <Row label="Capacity">{s.capacity}</Row>
        <Row label="Occupancy">{s.current_occupancy}</Row>
        <Row label="Available">{shelterAvailable(s)}</Row>
        <Row label="Utilization">{pct}%</Row>
      </div>
      {pct >= 85 && <p className="mt-2 text-xs text-red-300">At or above the 85% alert threshold.</p>}
    </div>
  );
}

function RoadDetails({ r }: { r: RoadSegment }) {
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <p className="font-medium">{r.name}</p>
        <ProvenanceBadge value={r.provenance} />
      </div>
      <p className={`mt-1 text-sm ${ROAD_STYLE[r.status].cls}`}>{ROAD_STYLE[r.status].label}</p>
      <div className="mt-3 divide-y divide-slate-800">
        <Row label="Serves">{r.serves_zone}</Row>
        <Row label="Updated">{timeAgo(r.updated_at)}</Row>
      </div>
      {r.note && <p className="mt-2 text-xs text-slate-400">{r.note}</p>}
      {r.provenance === "inferred" && (
        <p className="mt-2 rounded border border-dashed border-fuchsia-500/60 bg-fuchsia-950/30 p-2 text-xs text-fuchsia-200">
          AI-inferred potential accessibility problem, not a confirmed closure.
          Route verification required before use in any plan.
        </p>
      )}
    </div>
  );
}

// ---------- Legend ----------
function Legend() {
  const dot = (c: string) => <span className="inline-block h-3 w-3 rounded-full" style={{ background: c }} />;
  const line = (c: string, dashed = false) => (
    <span className="inline-block w-6 border-t-[3px]" style={{ borderColor: c, borderStyle: dashed ? "dashed" : "solid" }} />
  );
  return (
    <Panel title="Legend">
      <div className="space-y-3 text-xs text-slate-300">
        <div>
          <p className="mb-1 text-slate-500">Incidents (size = people affected)</p>
          <div className="flex flex-wrap gap-3">
            <span className="flex items-center gap-1">{dot(PRIORITY_COLOR.critical)} Critical</span>
            <span className="flex items-center gap-1">{dot(PRIORITY_COLOR.high)} High</span>
            <span className="flex items-center gap-1">{dot(PRIORITY_COLOR.moderate)} Moderate</span>
            <span className="flex items-center gap-1">{dot("#94a3b8")} Not assessed</span>
          </div>
        </div>
        <div>
          <p className="mb-1 text-slate-500">Roads</p>
          <div className="space-y-1">
            <span className="flex items-center gap-2">{line(ROAD_COLOR.open)} Open (known)</span>
            <span className="flex items-center gap-2">{line(ROAD_COLOR.partially_blocked)} Partially blocked (known)</span>
            <span className="flex items-center gap-2">{line(ROAD_COLOR.closed)} Closed (known)</span>
            <span className="flex items-center gap-2">{line(ROAD_COLOR.unknown, true)} AI-inferred: verify</span>
          </div>
        </div>
        <div>
          <p className="mb-1 text-slate-500">Resources (B boat · T team · A ambulance · V vehicle)</p>
          <div className="flex flex-wrap gap-3">
            <span className="flex items-center gap-1">{dot(RES_COLOR.available)} Available</span>
            <span className="flex items-center gap-1">{dot(RES_COLOR.deployed)} Deployed</span>
            <span className="flex items-center gap-1">{dot(RES_COLOR.returning)} Returning</span>
            <span className="flex items-center gap-1">{dot(RES_COLOR.unavailable)} Unavailable</span>
          </div>
        </div>
        <div>
          <p className="mb-1 text-slate-500">Shelters (S)</p>
          <div className="flex flex-wrap gap-3">
            <span className="flex items-center gap-1">{dot("#38bdf8")} &lt; 70%</span>
            <span className="flex items-center gap-1">{dot("#fbbf24")} 70–84%</span>
            <span className="flex items-center gap-1">{dot("#f87171")} ≥ 85%</span>
          </div>
        </div>
      </div>
    </Panel>
  );
}
