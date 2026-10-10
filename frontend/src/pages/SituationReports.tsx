import { useState } from "react";
import { Download, FileText } from "lucide-react";
import { api } from "../api";
import { useApi } from "../api/useApi";
import { useMutation } from "../api/useMutation";
import { Button, LoadState, Notice, PageHeader, Panel, StatCard } from "../components/ui";
import { RESOURCE_LABEL, shelterAvailable, timeAgo } from "../lib/format";
import type { SituationReportDetail, SituationSnapshot } from "../types";

async function load(): Promise<{ reports: SituationReportDetail[]; snap: SituationSnapshot }> {
  const [reports, snap] = await Promise.all([api.listReports(), api.getSituation()]);
  return { reports, snap };
}

export default function SituationReports() {
  const { data, error, reload } = useApi(load);
  const [latest, setLatest] = useState<SituationReportDetail | null>(null);
  const m = useMutation();
  if (!data) return <LoadState error={error} what="situation reports" />;
  const { snap } = data;
  const active = snap.incidents.filter((i) => !i.duplicate_of);

  return (
    <div className="space-y-6">
      <PageHeader title="Situation Reports"
        subtitle="PDF reports with all required sections, generated from the current database snapshot."
        action={<Button tone="primary" busy={m.busy === "gen"}
          onClick={() => m.run("gen", async () => setLatest(await api.generateReport()), reload)}>
          <FileText size={14} className="mr-1 inline" />Generate report</Button>} />
      {m.error && <Notice tone="error">{m.error}</Notice>}
      {latest && (
        <Notice>
          {latest.title} generated.{" "}
          <a className="text-sky-300 underline" href={api.reportPdfUrl(latest)} target="_blank" rel="noreferrer">Download PDF</a>
        </Notice>
      )}

      <Panel title="Preview of key sections (current data)">
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <StatCard label="Active incidents" value={active.length} />
          <StatCard label="Critical" value={active.filter((i) => i.priority?.level === "critical").length} tone="danger" />
          <StatCard label="Pending approvals" value={snap.actions.filter((a) => a.status === "pending").length} tone="warning" />
          <StatCard label="Free shelter spaces" value={snap.shelters.reduce((n, s) => n + shelterAvailable(s), 0)} />
        </div>
        <div className="mt-4 grid gap-4 text-sm md:grid-cols-2">
          <div>
            <p className="mb-1 text-xs uppercase text-slate-500">Shortages</p>
            {snap.shortages.length ? snap.shortages.map((s) => (
              <p key={s.resource_type}>{RESOURCE_LABEL[s.resource_type]}: deficit {s.deficit} ({s.required} required, {s.available} available)</p>
            )) : <p className="text-slate-500">None</p>}
          </div>
          <div>
            <p className="mb-1 text-xs uppercase text-slate-500">Data uncertainties</p>
            <ul className="list-disc pl-5 text-slate-300">
              {[...new Set(active.flatMap((i) => i.priority?.verification_needed ?? []))].slice(0, 6).map((v) => <li key={v}>{v}</li>)}
            </ul>
          </div>
        </div>
      </Panel>

      <Panel title={`Previous reports (${data.reports.length})`}>
        <ul className="divide-y divide-slate-800">
          {data.reports.map((r) => (
            <li key={r.id} className="py-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="font-medium">{r.title}</p>
                  <p className="text-xs text-slate-500">{timeAgo(r.generated_at)} · run {r.run_id ?? "none"} · narrative {r.narrative_llm ? "AI-written (grounding-checked)" : "template"}</p>
                </div>
                <a href={api.reportPdfUrl(r)} target="_blank" rel="noreferrer"
                  className="flex items-center gap-1 rounded-md border border-slate-700 px-3 py-1.5 text-xs hover:bg-slate-800">
                  <Download size={14} />PDF
                </a>
              </div>
              <p className="mt-2 text-sm text-slate-300">{r.narrative}</p>
            </li>
          ))}
          {!data.reports.length && <p className="text-sm text-slate-500">No reports yet.</p>}
        </ul>
      </Panel>
    </div>
  );
}
