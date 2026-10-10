import { useState } from "react";
import { api } from "../api";
import { useApi } from "../api/useApi";
import { useMutation } from "../api/useMutation";
import { Button, LoadState, Notice, PageHeader, Panel, StatCard, inputCls } from "../components/ui";
import { shelterAvailable, shelterUtilization } from "../lib/format";
import type { Shelter, ShelterStatus } from "../types";

/** Matches the backend default SHELTER_ALERT_THRESHOLD (alerts are raised by the backend, not here). */
const THRESHOLD = 0.85;

export default function ShelterManagement() {
  const { data, error, reload } = useApi(api.getShelters);
  const m = useMutation();
  if (!data) return <LoadState error={error} what="shelters" />;

  const open = data.filter((s) => s.status !== "closed");
  const free = open.reduce((n, s) => n + shelterAvailable(s), 0);
  const capacity = open.reduce((n, s) => n + s.capacity, 0);

  return (
    <div className="space-y-6">
      <PageHeader title="Shelter Management"
        subtitle={`Available capacity is always computed as capacity − occupancy. Alerts fire at ${THRESHOLD * 100}% utilization.`} />
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="Shelters" value={data.length} hint={`${open.length} open or full`} />
        <StatCard label="Free spaces" value={free} tone={free ? "good" : "danger"} />
        <StatCard label="Total capacity" value={capacity} />
        <StatCard label="At or above threshold" value={data.filter((s) => shelterUtilization(s) >= THRESHOLD).length} tone="warning" />
      </div>
      {m.error && <Notice tone="error">{m.error}</Notice>}
      <div className="grid gap-6 lg:grid-cols-2">
        {data.map((s) => (
          <ShelterCard key={`${s.id}-${s.current_occupancy}-${s.status}`} shelter={s} busy={m.busy === s.id}
            onSave={(occupancy, status) => m.run(s.id, () => api.patchShelter(s.id, {
              current_occupancy: occupancy, status: status === s.status ? null : status,
            }), reload)} />
        ))}
      </div>
    </div>
  );
}

function ShelterCard({ shelter: s, busy, onSave }: {
  shelter: Shelter; busy: boolean; onSave: (occupancy: number, status: ShelterStatus) => void;
}) {
  const [occ, setOcc] = useState(String(s.current_occupancy));
  const [status, setStatus] = useState<ShelterStatus>(s.status);
  const u = shelterUtilization(s);
  const bar = u >= THRESHOLD ? "bg-red-500" : u >= 0.7 ? "bg-amber-500" : "bg-sky-500";
  const valid = occ.trim() !== "" && Number.isInteger(Number(occ)) && Number(occ) >= 0;

  return (
    <Panel title={`${s.name} (${s.id})`} action={<span className="text-xs uppercase text-slate-400">{s.status}</span>}>
      <div className="grid grid-cols-4 gap-3 text-center text-sm">
        <div><p className="text-xs text-slate-500">Capacity</p><p className="text-lg">{s.capacity}</p></div>
        <div><p className="text-xs text-slate-500">Occupancy</p><p className="text-lg">{s.current_occupancy}</p></div>
        <div><p className="text-xs text-slate-500">Available</p><p className="text-lg text-emerald-300">{shelterAvailable(s)}</p></div>
        <div><p className="text-xs text-slate-500">Utilization</p><p className={`text-lg ${u >= THRESHOLD ? "text-red-300" : ""}`}>{Math.round(u * 100)}%</p></div>
      </div>
      <div className="relative mt-4 h-3 rounded bg-slate-800">
        <div className={`h-3 rounded ${bar}`} style={{ width: `${Math.min(100, u * 100)}%` }} />
        <div className="absolute top-[-4px] h-5 w-0.5 bg-amber-300" style={{ left: `${THRESHOLD * 100}%` }} title="Alert threshold" />
      </div>
      <p className="mt-1 text-right text-[10px] text-amber-300">threshold {THRESHOLD * 100}%</p>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="w-36 space-y-1 text-sm">
          <span className="text-xs text-slate-400">Occupancy</span>
          <input type="number" min={0} className={inputCls} value={occ} onChange={(e) => setOcc(e.target.value)} />
        </label>
        <label className="w-36 space-y-1 text-sm">
          <span className="text-xs text-slate-400">Status</span>
          <select className={inputCls} value={status} onChange={(e) => setStatus(e.target.value as ShelterStatus)}>
            <option value="open">open</option><option value="full">full</option><option value="closed">closed</option>
          </select>
        </label>
        <Button tone="primary" busy={busy} disabled={!valid} onClick={() => onSave(Number(occ), status)}>Update</Button>
      </div>
    </Panel>
  );
}
