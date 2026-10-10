import { useState } from "react";
import { Link } from "react-router-dom";
import { Clock, ShieldCheck } from "lucide-react";
import { api } from "../api";
import { useApi } from "../api/useApi";
import { useMutation } from "../api/useMutation";
import {
  Button, LoadState, Notice, PageHeader, Panel, PriorityBadge, ProvenanceBadge, inputCls,
} from "../components/ui";
import { RESOURCE_LABEL, minutesUntil, pretty, timeAgo } from "../lib/format";
import type { ActionEvent, Resource, ResponseAction, SituationSnapshot } from "../types";

export default function ApprovalCenter() {
  const { data, error, reload } = useApi(api.getSituation);
  if (!data) return <LoadState error={error} what="pending approvals" />;
  const pending = data.actions.filter((a) => a.status === "pending")
    .sort((a, b) => (a.due_by ?? "").localeCompare(b.due_by ?? ""));

  return (
    <div className="space-y-6">
      <PageHeader title="Approval Center"
        subtitle="Every recommendation waits here for a coordinator. Approving reserves the resources; nothing is reserved before." />
      {pending.length === 0 && (
        <Panel title="Nothing to approve">
          <p className="text-sm text-slate-400">No pending recommendations. Run an assessment from Response Planning.</p>
        </Panel>
      )}
      {pending.map((a) => <ActionCard key={a.id} action={a} snap={data} onChange={reload} />)}
    </div>
  );
}

function ActionCard({ action: a, snap, onChange }: { action: ResponseAction; snap: SituationSnapshot; onChange: () => void }) {
  const m = useMutation();
  const [comment, setComment] = useState("");
  const [modifying, setModifying] = useState(false);
  const [events, setEvents] = useState<ActionEvent[] | null>(null);
  const incident = snap.incidents.find((i) => i.id === a.incident_id);
  const due = a.due_by ? minutesUntil(a.due_by) : null;
  const text = comment.trim() || undefined;
  const act = (key: string, fn: () => Promise<unknown>) => m.run(key, fn, () => { setComment(""); onChange(); });

  return (
    <section className="rounded-xl border border-amber-700/40 bg-slate-900/60">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 px-4 py-3">
        <div className="flex items-center gap-3">
          <ShieldCheck size={18} className="text-amber-300" />
          <span className="font-mono text-xs text-slate-400">{a.id}</span>
          <PriorityBadge level={a.priority} />
          <Link to={`/incidents/${a.incident_id}`} className="text-sm text-sky-400 hover:underline">{a.incident_id} · {incident?.title}</Link>
        </div>
        {due !== null && (
          <span className={`flex items-center gap-1 text-xs ${due < 0 ? "text-red-300" : "text-slate-400"}`}>
            <Clock size={12} />{due < 0 ? `Overdue by ${-due} min` : `Decision due in ${due} min`}
          </span>
        )}
      </header>
      <div className="grid gap-4 p-4 lg:grid-cols-3">
        <div className="space-y-3 lg:col-span-2">
          <p className="text-base">{a.description}</p>
          <div>
            <p className="mb-1 text-xs uppercase text-slate-500">Rationale</p>
            <ul className="list-disc space-y-0.5 pl-5 text-sm text-slate-300">{a.rationale.map((r) => <li key={r}>{r}</li>)}</ul>
          </div>
          {a.assumptions.length > 0 && (
            <div>
              <p className="mb-1 text-xs uppercase text-slate-500">Assumptions (check before approving)</p>
              <ul className="space-y-1">{a.assumptions.map((x) => <li key={x}><Notice tone="verify">{x}</Notice></li>)}</ul>
            </div>
          )}
        </div>
        <div>
          <p className="mb-1 text-xs uppercase text-slate-500">Inputs used</p>
          <ul className="divide-y divide-slate-800 text-xs">
            {a.inputs_used.map((i) => (
              <li key={i.label} className="flex items-start justify-between gap-2 py-1.5">
                <span><span className="text-slate-400">{i.label}: </span>{i.value}</span>
                <ProvenanceBadge value={i.provenance} />
              </li>
            ))}
          </ul>
          <p className="mb-1 mt-3 text-xs uppercase text-slate-500">Resources</p>
          <p className="font-mono text-xs">{a.resource_ids.join(", ")}</p>
        </div>
      </div>

      {modifying && (
        <ModifyPicker action={a} resources={snap.resources} busy={m.busy === "modify"} onCancel={() => setModifying(false)}
          onSave={(ids) => m.run("modify", () => api.modify(a.id, ids, text), () => { setModifying(false); onChange(); })} />
      )}

      <footer className="space-y-3 border-t border-slate-800 px-4 py-3">
        {m.error && <Notice tone="error">{m.error}</Notice>}
        <input className={inputCls} placeholder="Comment (recorded in the audit trail)" value={comment} onChange={(e) => setComment(e.target.value)} />
        <div className="flex flex-wrap gap-2">
          <Button tone="good" busy={m.busy === "approve"} onClick={() => act("approve", () => api.approve(a.id, text))}>Approve</Button>
          <Button tone="danger" busy={m.busy === "reject"} onClick={() => act("reject", () => api.reject(a.id, text))}>Reject</Button>
          <Button onClick={() => setModifying((x) => !x)}>Modify resources</Button>
          <Button busy={m.busy === "reassess"} onClick={() => act("reassess", () => api.reassess(a.id, text))}>Request reassessment</Button>
          <Button busy={m.busy === "comment"} disabled={!text} onClick={() => act("comment", () => api.comment(a.id, text!))}>Comment</Button>
          <Button onClick={async () => setEvents(events ? null : await api.actionEvents(a.id))}>{events ? "Hide" : "Show"} audit trail</Button>
        </div>
        {events && <AuditTrail events={events} />}
      </footer>
    </section>
  );
}

function ModifyPicker({ action, resources, busy, onSave, onCancel }: {
  action: ResponseAction; resources: Resource[]; busy: boolean; onSave: (ids: string[]) => void; onCancel: () => void;
}) {
  const [picked, setPicked] = useState<string[]>(action.resource_ids);
  // Only resources that are free right now can be chosen. The backend re-checks availability and access.
  const options = resources.filter((r) => r.status === "available" || action.resource_ids.includes(r.id));
  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  const byType = options.reduce<Record<string, Resource[]>>((acc, r) => ({ ...acc, [r.type]: [...(acc[r.type] ?? []), r] }), {});
  return (
    <div className="space-y-3 border-t border-slate-800 bg-slate-950/40 px-4 py-3">
      <p className="text-xs text-slate-400">Choose from currently available resources. The backend rejects units that cannot reach the zone (known road closure).</p>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {Object.entries(byType).map(([type, list]) => (
          <div key={type}>
            <p className="mb-1 text-xs uppercase text-slate-500">{RESOURCE_LABEL[type as Resource["type"]]}</p>
            {list.map((r) => (
              <label key={r.id} className="flex items-center gap-2 text-xs">
                <input type="checkbox" checked={picked.includes(r.id)} onChange={() => toggle(r.id)} />
                <span className="font-mono">{r.id}</span><span className="text-slate-500">cap {r.capacity}</span>
              </label>
            ))}
          </div>
        ))}
      </div>
      <div className="flex gap-2">
        <Button tone="primary" busy={busy} disabled={!picked.length} onClick={() => onSave(picked)}>Save modification</Button>
        <Button onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}

export function AuditTrail({ events }: { events: ActionEvent[] }) {
  return (
    <ol className="space-y-1 border-l border-slate-700 pl-4 text-xs">
      {events.map((e) => (
        <li key={e.id}>
          <span className="text-slate-500">{timeAgo(e.at)}</span> · <span className="font-medium text-slate-200">{pretty(e.event)}</span>
          {" "}by {e.actor}{e.comment && <span className="text-slate-400">: "{e.comment}"</span>}
        </li>
      ))}
    </ol>
  );
}
