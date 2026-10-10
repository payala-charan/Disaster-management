import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { History } from "lucide-react";
import { api } from "../api";
import { useApi } from "../api/useApi";
import { useMutation } from "../api/useMutation";
import {
  Button, LoadState, Notice, PageHeader, Panel, StatCard, StyledBadge, inputCls,
} from "../components/ui";
import {
  MANUAL_TRANSITIONS, RESOURCE_LABEL, RESOURCE_STATUS_STYLE, formatTime, pretty, timeAgo,
} from "../lib/format";
import type { Resource, ResourceStatus, ResourceStatusChange, ResourceType } from "../types";

const STATUSES = Object.keys(RESOURCE_STATUS_STYLE) as ResourceStatus[];
const TYPES = Object.keys(RESOURCE_LABEL) as ResourceType[];

export default function ResourceCenter() {
  const { data, error, reload } = useApi(api.getResources);
  const [type, setType] = useState<ResourceType | "all">("all");
  const [status, setStatus] = useState<ResourceStatus | "all">("all");
  const [editing, setEditing] = useState<string | null>(null);
  const [history, setHistory] = useState<Record<string, ResourceStatusChange[]>>({});
  const m = useMutation();

  if (!data) return <LoadState error={error} what="resources" />;

  const rows = data.filter((r) => (type === "all" || r.type === type) && (status === "all" || r.status === status));
  const count = (s: ResourceStatus) => data.filter((r) => r.status === s).length;

  const toggleHistory = async (id: string) => {
    if (history[id]) {
      setHistory((prev) => Object.fromEntries(Object.entries(prev).filter(([k]) => k !== id)));
      return;
    }
    const h = await api.resourceHistory(id);
    setHistory((prev) => ({ ...prev, [id]: h }));
  };

  return (
    <div className="space-y-6">
      <PageHeader title="Resource Center" subtitle="Registry of response resources. Reserving and deploying happen only through approved actions." />

      <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
        {STATUSES.map((s) => (
          <StatCard key={s} label={RESOURCE_STATUS_STYLE[s].label} value={count(s)}
            tone={s === "available" ? "good" : s === "unavailable" ? "danger" : "default"} />
        ))}
      </div>

      <Panel title={`Resources (${rows.length})`}>
        <div className="mb-4 flex flex-wrap gap-3">
          <select className={`${inputCls} max-w-[220px]`} value={type} onChange={(e) => setType(e.target.value as ResourceType | "all")}>
            <option value="all">All types</option>
            {TYPES.map((t) => <option key={t} value={t}>{RESOURCE_LABEL[t]}</option>)}
          </select>
          <select className={`${inputCls} max-w-[200px]`} value={status} onChange={(e) => setStatus(e.target.value as ResourceStatus | "all")}>
            <option value="all">All statuses</option>
            {STATUSES.map((s) => <option key={s} value={s}>{RESOURCE_STATUS_STYLE[s].label}</option>)}
          </select>
        </div>
        {m.error && <div className="mb-3"><Notice tone="error">{m.error}</Notice></div>}

        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase text-slate-500">
              <tr className="border-b border-slate-800">
                <th className="py-2 pr-3">ID</th><th className="py-2 pr-3">Resource</th><th className="py-2 pr-3">Status</th>
                <th className="py-2 pr-3">Capacity</th><th className="py-2 pr-3">Assignment</th>
                <th className="py-2 pr-3">Expected available</th><th className="py-2 text-right">Controls</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {rows.map((r) => (
                <Fragment key={r.id}>
                  <tr className="align-top hover:bg-slate-800/30">
                    <td className="py-2 pr-3 font-mono text-xs">{r.id}</td>
                    <td className="py-2 pr-3">
                      <p>{r.name}</p>
                      <p className="text-xs text-slate-500">{RESOURCE_LABEL[r.type]} · {r.base_name} · {r.capabilities.map(pretty).join(", ")}</p>
                    </td>
                    <td className="py-2 pr-3"><StyledBadge style={RESOURCE_STATUS_STYLE[r.status]} /></td>
                    <td className="py-2 pr-3">{r.capacity}</td>
                    <td className="py-2 pr-3">
                      {r.assigned_incident_id
                        ? <Link to={`/incidents/${r.assigned_incident_id}`} className="text-sky-400 hover:underline">{r.assigned_incident_id}</Link>
                        : <span className="text-slate-500">–</span>}
                    </td>
                    <td className="py-2 pr-3 text-xs text-slate-400">
                      {r.expected_available_at ? `${formatTime(r.expected_available_at)}` : r.status === "unavailable" ? "Unknown" : "–"}
                    </td>
                    <td className="py-2 text-right">
                      <div className="flex justify-end gap-2">
                        <Button onClick={() => toggleHistory(r.id)} title="Status history"><History size={14} /></Button>
                        <Button onClick={() => setEditing(editing === r.id ? null : r.id)}>Change status</Button>
                      </div>
                    </td>
                  </tr>
                  {editing === r.id && (
                    <tr><td colSpan={7} className="pb-3">
                      <StatusForm resource={r} busy={m.busy === r.id} onCancel={() => setEditing(null)}
                        onSubmit={(to, reason, hours) => m.run(r.id, () => api.setResourceStatus(r.id, {
                          status: to, reason,
                          expected_available_at: hours ? new Date(Date.now() + hours * 3_600_000).toISOString() : null,
                        }), () => { setEditing(null); reload(); })} />
                    </td></tr>
                  )}
                  {history[r.id] && (
                    <tr><td colSpan={7} className="pb-3">
                      <ul className="ml-4 space-y-1 border-l border-slate-700 pl-4 text-xs text-slate-400">
                        {history[r.id].map((h, n) => (
                          <li key={n}>{timeAgo(h.changed_at)}: {h.from_status ?? "registered"} → <span className="text-slate-200">{h.to_status}</span> · {h.reason}</li>
                        ))}
                      </ul>
                    </td></tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}

function StatusForm({ resource, busy, onSubmit, onCancel }: {
  resource: Resource; busy: boolean;
  onSubmit: (to: ResourceStatus, reason: string, hours: number | null) => void; onCancel: () => void;
}) {
  const options = MANUAL_TRANSITIONS[resource.status];
  const [to, setTo] = useState<ResourceStatus>(options[0]);
  const [reason, setReason] = useState("");
  const [hours, setHours] = useState("");
  return (
    <div className="ml-4 flex flex-wrap items-end gap-3 rounded-md border border-slate-700 bg-slate-950/60 p-3 text-sm">
      <label className="space-y-1">
        <span className="text-xs text-slate-400">New status (valid transitions only)</span>
        <select className={inputCls} value={to} onChange={(e) => setTo(e.target.value as ResourceStatus)}>
          {options.map((s) => <option key={s} value={s}>{RESOURCE_STATUS_STYLE[s].label}</option>)}
        </select>
      </label>
      <label className="min-w-[240px] flex-1 space-y-1">
        <span className="text-xs text-slate-400">Reason (required)</span>
        <input className={inputCls} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Engine failure reported by crew" />
      </label>
      {to === "unavailable" && (
        <label className="w-40 space-y-1">
          <span className="text-xs text-slate-400">Back in (hours, optional)</span>
          <input type="number" min={0} className={inputCls} value={hours} onChange={(e) => setHours(e.target.value)} />
        </label>
      )}
      <Button tone="primary" busy={busy} disabled={!reason.trim()}
        onClick={() => onSubmit(to, reason.trim(), hours ? Number(hours) : null)}>Apply</Button>
      <Button onClick={onCancel}>Cancel</Button>
      {resource.assigned_incident_id && to === "unavailable" && (
        <p className="w-full text-xs text-amber-300">
          {resource.id} is assigned to {resource.assigned_incident_id}: its action will be flagged for coordinator review and the plan recalculated.
        </p>
      )}
    </div>
  );
}
