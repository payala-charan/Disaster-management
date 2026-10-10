import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle } from "lucide-react";
import { api } from "../api";
import { useApi } from "../api/useApi";
import { useMutation } from "../api/useMutation";
import { Button, LoadState, Notice, PageHeader, PriorityBadge, inputCls } from "../components/ui";
import { ACTION_STATUS_STYLE, formatTime, minutesUntil } from "../lib/format";
import type { ActionStatus, ResponseAction } from "../types";

const COLUMNS: { status: ActionStatus; title: string }[] = [
  { status: "pending", title: "Pending approval" },
  { status: "approved", title: "Approved (resources reserved)" },
  { status: "in_progress", title: "In progress (deployed)" },
  { status: "completed", title: "Completed" },
];

const isOverdue = (a: ResponseAction) =>
  (a.status === "pending" || a.status === "approved") && a.due_by !== null && minutesUntil(a.due_by) < 0;

export default function ActionTracker() {
  const { data, error, reload } = useApi(() => api.listActions());
  const [showClosed, setShowClosed] = useState(false);
  if (!data) return <LoadState error={error} what="actions" />;
  const closed = data.filter((a) => a.status === "rejected" || a.status === "superseded");

  return (
    <div className="space-y-6">
      <PageHeader title="Action Tracker" subtitle="Pending → Approved → In progress → Completed. Each step is a coordinator decision." />
      <div className="grid gap-4 xl:grid-cols-4">
        {COLUMNS.map((c) => {
          const items = data.filter((a) => a.status === c.status);
          return (
            <div key={c.status} className="rounded-xl border border-slate-800 bg-slate-900/40">
              <h3 className="border-b border-slate-800 px-3 py-2 text-sm font-semibold">{c.title} <span className="text-slate-500">({items.length})</span></h3>
              <div className="space-y-3 p-3">
                {items.map((a) => <ActionTile key={a.id} action={a} onChange={reload} />)}
                {!items.length && <p className="text-xs text-slate-500">None</p>}
              </div>
            </div>
          );
        })}
      </div>
      <div>
        <Button onClick={() => setShowClosed((x) => !x)}>{showClosed ? "Hide" : "Show"} rejected / superseded ({closed.length})</Button>
        {showClosed && (
          <ul className="mt-3 divide-y divide-slate-800 text-sm">
            {closed.map((a) => (
              <li key={a.id} className="flex items-center gap-3 py-2">
                <span className="font-mono text-xs text-slate-500">{a.id}</span>
                <span className={`rounded px-2 py-0.5 text-xs ring-1 ${ACTION_STATUS_STYLE[a.status].cls}`}>{ACTION_STATUS_STYLE[a.status].label}</span>
                <span className="text-slate-400">{a.description}</span>
                {a.remarks.at(-1) && <span className="text-xs text-slate-500">{a.remarks.at(-1)}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function ActionTile({ action: a, onChange }: { action: ResponseAction; onChange: () => void }) {
  const m = useMutation();
  const [remark, setRemark] = useState("");
  const overdue = isOverdue(a);
  return (
    <div className={`rounded-md border p-3 text-sm ${overdue ? "border-red-600 bg-red-950/20" : "border-slate-800 bg-slate-900/60"}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-xs text-slate-400">{a.id}</span>
        <PriorityBadge level={a.priority} />
      </div>
      <Link to={`/incidents/${a.incident_id}`} className="mt-1 block text-sky-400 hover:underline">{a.incident_id}</Link>
      <p className="mt-1 text-xs text-slate-300">{a.resource_ids.join(", ")}</p>
      <p className="mt-1 text-[11px] text-slate-500">
        created {formatTime(a.created_at)}
        {a.approved_at && ` · approved ${formatTime(a.approved_at)}`}
        {a.started_at && ` · started ${formatTime(a.started_at)}`}
        {a.completed_at && ` · done ${formatTime(a.completed_at)}`}
      </p>
      {overdue && <p className="mt-1 flex items-center gap-1 text-xs text-red-300"><AlertTriangle size={12} />Overdue since {formatTime(a.due_by)}</p>}
      {a.remarks.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-[11px] text-slate-400">{a.remarks.slice(-3).map((r, n) => <li key={n}>• {r}</li>)}</ul>
      )}
      {m.error && <div className="mt-2"><Notice tone="error">{m.error}</Notice></div>}
      <div className="mt-2 flex flex-wrap gap-2">
        {a.status === "pending" && <Link to="/approvals" className="text-xs text-amber-300 hover:underline">Decide in Approval Center →</Link>}
        {a.status === "approved" && <Button tone="primary" busy={m.busy === "start"} onClick={() => m.run("start", () => api.start(a.id), onChange)}>Start</Button>}
        {a.status === "in_progress" && <Button tone="good" busy={m.busy === "done"} onClick={() => m.run("done", () => api.complete(a.id), onChange)}>Complete</Button>}
      </div>
      <div className="mt-2 flex gap-2">
        <input className={`${inputCls} py-1 text-xs`} placeholder="Remark" value={remark} onChange={(e) => setRemark(e.target.value)} />
        <Button disabled={!remark.trim()} busy={m.busy === "remark"}
          onClick={() => m.run("remark", () => api.comment(a.id, remark.trim()), () => { setRemark(""); onChange(); })}>Add</Button>
      </div>
    </div>
  );
}
