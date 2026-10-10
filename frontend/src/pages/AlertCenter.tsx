import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useApi } from "../api/useApi";
import { useMutation } from "../api/useMutation";
import { Button, LoadState, Notice, PageHeader, Panel, StatCard, inputCls } from "../components/ui";
import { SEVERITY_STYLE, pretty, timeAgo } from "../lib/format";
import type { AlertKind, AlertSeverity } from "../types";

const linkFor = (id: string | null) =>
  !id ? null : id.startsWith("INC-") ? `/incidents/${id}` : id.startsWith("RD-") ? "/routes"
    : id.startsWith("SHL-") ? "/shelters" : id.startsWith("ACT-") ? "/actions" : "/resources";

export default function AlertCenter() {
  const { data, error, reload } = useApi(() => api.listAlerts());
  const [severity, setSeverity] = useState<AlertSeverity | "all">("all");
  const [kind, setKind] = useState<AlertKind | "all">("all");
  const [showAcked, setShowAcked] = useState(false);
  const m = useMutation();
  if (!data) return <LoadState error={error} what="alerts" />;

  const kinds = [...new Set(data.map((a) => a.kind))];
  const rows = data.filter((a) => (severity === "all" || a.severity === severity) && (kind === "all" || a.kind === kind)
    && (showAcked || !a.acknowledged));
  const open = data.filter((a) => !a.acknowledged);

  return (
    <div className="space-y-6">
      <PageHeader title="Alert Center" subtitle="Live alerts pushed by the backend (critical incidents, route closures, shelter capacity, weather, overdue actions)." />
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="Unacknowledged" value={open.length} tone={open.length ? "danger" : "good"} />
        <StatCard label="Critical" value={open.filter((a) => a.severity === "critical").length} tone="danger" />
        <StatCard label="Warning" value={open.filter((a) => a.severity === "warning").length} tone="warning" />
        <StatCard label="Total" value={data.length} />
      </div>
      <Panel title={`Alerts (${rows.length})`}>
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <select className={`${inputCls} max-w-[180px]`} value={severity} onChange={(e) => setSeverity(e.target.value as AlertSeverity | "all")}>
            <option value="all">All severities</option>
            {(Object.keys(SEVERITY_STYLE) as AlertSeverity[]).map((s) => <option key={s} value={s}>{SEVERITY_STYLE[s].label}</option>)}
          </select>
          <select className={`${inputCls} max-w-[220px]`} value={kind} onChange={(e) => setKind(e.target.value as AlertKind | "all")}>
            <option value="all">All kinds</option>
            {kinds.map((k) => <option key={k} value={k}>{pretty(k)}</option>)}
          </select>
          <label className="flex items-center gap-2 text-sm text-slate-300">
            <input type="checkbox" checked={showAcked} onChange={() => setShowAcked((x) => !x)} /> Show acknowledged
          </label>
        </div>
        {m.error && <div className="mb-3"><Notice tone="error">{m.error}</Notice></div>}
        <ul className="space-y-2">
          {rows.map((a) => {
            const st = SEVERITY_STYLE[a.severity];
            const to = linkFor(a.related_id);
            return (
              <li key={a.id} className={`flex items-start justify-between gap-4 rounded-md border-l-4 bg-slate-800/40 p-3 text-sm ${st.border} ${a.acknowledged ? "opacity-50" : ""}`}>
                <div>
                  <p className={`text-xs font-semibold uppercase ${st.text}`}>{st.label} · {pretty(a.kind)} · <span className="font-mono">{a.id}</span></p>
                  <p className="mt-1">{a.message}</p>
                  <p className="mt-1 text-xs text-slate-500">
                    {timeAgo(a.created_at)}{to && <> · <Link to={to} className="text-sky-400 hover:underline">{a.related_id}</Link></>}
                  </p>
                </div>
                {!a.acknowledged && (
                  <Button busy={m.busy === a.id} onClick={() => m.run(a.id, () => api.ackAlert(a.id), reload)}>Acknowledge</Button>
                )}
              </li>
            );
          })}
          {!rows.length && <p className="text-sm text-slate-500">No alerts match the filters.</p>}
        </ul>
      </Panel>
    </div>
  );
}
