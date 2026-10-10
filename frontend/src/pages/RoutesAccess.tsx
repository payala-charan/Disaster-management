import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useApi } from "../api/useApi";
import { useMutation } from "../api/useMutation";
import {
  Button, LoadState, Notice, PageHeader, Panel, ProvenanceBadge, inputCls,
} from "../components/ui";
import { ROAD_STYLE, timeAgo } from "../lib/format";
import type { AccessAnalysis, Provenance, RoadSegment, RoadStatus, SituationSnapshot } from "../types";

const ACCESS_STYLE: Record<AccessAnalysis["access_status"], { label: string; cls: string }> = {
  accessible: { label: "Accessible by road", cls: "text-emerald-300 border-emerald-700/60" },
  partially_accessible: { label: "Partially accessible", cls: "text-amber-300 border-amber-700/60" },
  inaccessible_by_road: { label: "Inaccessible by road (known closure)", cls: "text-red-300 border-red-700/60" },
  unknown: { label: "Unknown: route verification required", cls: "text-violet-300 border-violet-700/60" },
};

async function loadAccess(): Promise<{ snap: SituationSnapshot; access: AccessAnalysis[] }> {
  const snap = await api.getSituation();
  const primary = snap.incidents.filter((i) => !i.duplicate_of);
  const access = await Promise.all(primary.map((i) => api.getAccess(i.id)));
  return { snap, access };
}

export default function RoutesAccess() {
  const { data, error, reload } = useApi(loadAccess);
  const m = useMutation();
  if (!data) return <LoadState error={error} what="route analysis" />;

  return (
    <div className="space-y-6">
      <PageHeader title="Route & Accessibility"
        subtitle="Known road data (registry) and AI-inferred potential problems are shown separately and never treated as equal." />

      <div className="grid gap-6 xl:grid-cols-2">
        {data.access.map((a) => {
          const inc = data.snap.incidents.find((i) => i.id === a.incident_id)!;
          const style = ACCESS_STYLE[a.access_status];
          return (
            <Panel key={a.incident_id} title={`${a.incident_id} · ${a.zone}`}
              action={<Link to={`/incidents/${a.incident_id}`} className="text-xs text-sky-400 hover:underline">{inc.title}</Link>}>
              <p className={`rounded border px-3 py-2 text-sm font-medium ${style.cls}`}>{style.label}</p>
              <div className="mt-4 grid gap-4 md:grid-cols-2">
                <div>
                  <p className="mb-2 text-xs uppercase text-sky-300">Known / reported routes</p>
                  {a.known_routes.length ? a.known_routes.map((r) => (
                    <div key={r.road_id} className="mb-2 text-sm">
                      <div className="flex items-center justify-between gap-2"><span>{r.name}</span><ProvenanceBadge value={r.provenance} /></div>
                      <p className={`text-xs ${ROAD_STYLE[r.status].cls}`}>{ROAD_STYLE[r.status].label}</p>
                      {r.note && <p className="text-xs text-slate-500">{r.note}</p>}
                    </div>
                  )) : <p className="text-sm text-violet-300">No confirmed route in the registry.</p>}
                </div>
                <div className="rounded-md border border-dashed border-fuchsia-500/40 p-2">
                  <p className="mb-2 text-xs uppercase text-fuchsia-300">AI-inferred potential issues (unconfirmed)</p>
                  {a.inferred_issues.length ? a.inferred_issues.map((r) => (
                    <div key={r.road_id} className="mb-2 text-sm">
                      <div className="flex items-center justify-between gap-2"><span>{r.name}</span><ProvenanceBadge value={r.provenance} /></div>
                      {r.note && <p className="text-xs text-slate-400">{r.note}</p>}
                    </div>
                  )) : <p className="text-sm text-slate-500">None.</p>}
                </div>
              </div>
              <p className="mb-1 mt-4 text-xs uppercase text-slate-500">Travel estimates (fastest unit per base)</p>
              {a.travel_estimates.length ? (
                <table className="w-full text-sm">
                  <tbody className="divide-y divide-slate-800">
                    {a.travel_estimates.map((t) => (
                      <tr key={t.from_id}>
                        <td className="py-1">{t.from_name}</td>
                        <td className="py-1 text-right">{t.distance_km.toFixed(1)} km</td>
                        <td className="py-1 text-right">{Math.round(t.duration_min)} min</td>
                        <td className="py-1 text-right text-xs">
                          {t.source === "osrm" ? <span className="text-sky-300">routed (OSRM)</span> : <span className="text-amber-300">estimated</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : <p className="text-sm text-violet-300">Route verification required: no travel data.</p>}
              {a.access_status === "inaccessible_by_road" && (
                <p className="mt-2 text-xs text-slate-400">Travel times show distance only. OSRM does not know about flood closures; the road registry decides reachability.</p>
              )}
              {a.verification_needed.length > 0 && (
                <ul className="mt-3 space-y-1">
                  {a.verification_needed.map((v) => <li key={v}><Notice tone="verify">{v}</Notice></li>)}
                </ul>
              )}
            </Panel>
          );
        })}
      </div>

      <Panel title="Road registry">
        {m.error && <div className="mb-3"><Notice tone="error">{m.error}</Notice></div>}
        <table className="w-full text-left text-sm">
          <thead className="text-xs uppercase text-slate-500">
            <tr className="border-b border-slate-800">
              <th className="py-2 pr-3">Road</th><th className="py-2 pr-3">Zone</th><th className="py-2 pr-3">Status</th>
              <th className="py-2 pr-3">Provenance</th><th className="py-2 pr-3">Updated</th><th className="py-2">Update (triggers replanning)</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {data.snap.roads.map((r) => (
              <RoadRow key={`${r.id}-${r.status}-${r.provenance}-${r.updated_at}`} road={r} busy={m.busy === r.id}
                onSave={(status, provenance, note) => m.run(r.id, () => api.patchRoad(r.id, { status, provenance, note: note || null }), reload)} />
            ))}
          </tbody>
        </table>
      </Panel>
    </div>
  );
}

function RoadRow({ road: r, busy, onSave }: {
  road: RoadSegment; busy: boolean; onSave: (s: RoadStatus, p: Provenance, note: string) => void;
}) {
  const [status, setStatus] = useState<RoadStatus>(r.status);
  const [prov, setProv] = useState<Provenance>(r.provenance);
  const [note, setNote] = useState("");
  const changed = status !== r.status || prov !== r.provenance || note.trim() !== "";
  return (
    <tr className="align-top">
      <td className="py-2 pr-3"><p>{r.name}</p><p className="font-mono text-xs text-slate-500">{r.id}</p>{r.note && <p className="text-xs text-slate-500">{r.note}</p>}</td>
      <td className="py-2 pr-3">{r.serves_zone}</td>
      <td className={`py-2 pr-3 ${ROAD_STYLE[r.status].cls}`}>{ROAD_STYLE[r.status].label}</td>
      <td className="py-2 pr-3"><ProvenanceBadge value={r.provenance} /></td>
      <td className="py-2 pr-3 text-xs text-slate-400">{timeAgo(r.updated_at)}</td>
      <td className="py-2">
        <div className="flex flex-wrap gap-2">
          <select className={`${inputCls} w-40`} value={status} onChange={(e) => setStatus(e.target.value as RoadStatus)}>
            {(Object.keys(ROAD_STYLE) as RoadStatus[]).map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
          </select>
          <select className={`${inputCls} w-32`} value={prov} onChange={(e) => setProv(e.target.value as Provenance)} title="Is this confirmed?">
            <option value="known">known</option><option value="reported">reported</option><option value="inferred">inferred</option>
          </select>
          <input className={`${inputCls} w-48`} placeholder="Note / source" value={note} onChange={(e) => setNote(e.target.value)} />
          <Button tone="primary" busy={busy} disabled={!changed} onClick={() => onSave(status, prov, note.trim())}>Save</Button>
        </div>
      </td>
    </tr>
  );
}
