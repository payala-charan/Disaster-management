import { useState } from "react";
import { Link } from "react-router-dom";
import { Bot, ChevronDown, ChevronRight, Cpu, Play, Star } from "lucide-react";
import { api } from "../api";
import { useApi } from "../api/useApi";
import { useMutation } from "../api/useMutation";
import { Button, LoadState, Notice, PageHeader, Panel, PriorityBadge } from "../components/ui";
import { RESOURCE_LABEL, pretty, timeAgo } from "../lib/format";
import type { AgentRun, CriticFinding, Plan, RunResult } from "../types";

const FINDING_STYLE: Record<CriticFinding["severity"], string> = {
  blocking: "border-red-700/60 bg-red-950/40 text-red-200",
  warning: "border-amber-700/60 bg-amber-950/30 text-amber-200",
  info: "border-slate-700 bg-slate-800/40 text-slate-300",
};

async function loadPlanning(): Promise<{ plans: Plan[]; run: AgentRun | null }> {
  const [plans, runs] = await Promise.all([api.latestPlans(), api.listRuns(1)]);
  return { plans, run: runs[0] ?? null };
}

export default function ResponsePlanning() {
  const { data, error, reload } = useApi(loadPlanning);
  const [last, setLast] = useState<RunResult | null>(null);
  const m = useMutation();
  if (!data) return <LoadState error={error} what="response plans" />;

  const runNow = () => m.run("run", async () => setLast(await api.runAssessment()), reload);

  return (
    <div className="space-y-6">
      <PageHeader title="Response Planning"
        subtitle="Three alternative plans built by the deterministic allocation engine and checked by the reviewer agent. Nothing here is dispatched."
        action={<Button tone="primary" busy={m.busy === "run"} onClick={runNow}><Play size={14} className="mr-1 inline" />Run assessment</Button>} />
      {m.error && <Notice tone="error">{m.error}</Notice>}
      {last && <Notice>{last.run_id}: {last.summary}</Notice>}

      {data.plans.length === 0 ? (
        <Panel title="No plans yet"><p className="text-sm text-slate-400">Run an assessment to generate plans A, B and C.</p></Panel>
      ) : (
        <div className="grid gap-4 xl:grid-cols-3">
          {data.plans.map((p) => <PlanCard key={p.id} plan={p} />)}
        </div>
      )}

      {data.run && <AgentTrace run={data.run} />}
    </div>
  );
}

function PlanCard({ plan: p }: { plan: Plan }) {
  const blocking = p.critic_findings.filter((f) => f.severity === "blocking");
  const nonInfo = p.critic_findings.filter((f) => f.severity !== "info");
  return (
    <section className={`rounded-xl border bg-slate-900/60 ${p.is_recommended ? "border-emerald-600/70" : "border-slate-800"}`}>
      <header className="border-b border-slate-800 px-4 py-3">
        <div className="flex items-center justify-between gap-2">
          <h3 className="font-semibold">{p.title}</h3>
          {p.is_recommended && <span className="flex items-center gap-1 rounded bg-emerald-600/20 px-2 py-0.5 text-xs text-emerald-300"><Star size={12} />Recommended</span>}
        </div>
        <p className="mt-1 text-xs text-slate-400">{p.strategy}</p>
        <p className="mt-1 text-xs text-slate-500">{p.id} · score {p.plan_score} · {p.status}</p>
      </header>
      <div className="space-y-4 p-4 text-sm">
        <p className="leading-relaxed text-slate-300">{p.explanation}</p>

        <div className="space-y-3">
          {p.allocations.map((a) => (
            <div key={a.incident_id} className="rounded-md border border-slate-800 p-3">
              <div className="flex items-center justify-between">
                <Link to={`/incidents/${a.incident_id}`} className="font-mono text-xs text-sky-400 hover:underline">{a.incident_id}</Link>
                <PriorityBadge level={a.priority} />
              </div>
              {a.resources.length ? (
                <ul className="mt-2 space-y-1">
                  {a.resources.map((r) => (
                    <li key={r.resource_id} className="text-xs">
                      <span className="font-mono text-slate-200">{r.resource_id}</span>{" "}
                      <span className="text-slate-400">{r.reason}</span>
                    </li>
                  ))}
                </ul>
              ) : <p className="mt-2 text-xs text-amber-300">No resources allocated.</p>}
              {a.unmet.length > 0 && (
                <p className="mt-2 text-xs text-red-300">
                  Unmet: {a.unmet.map((u) => `${u.shortfall} ${RESOURCE_LABEL[u.resource_type].toLowerCase()}`).join(", ")}
                </p>
              )}
              {a.verification_required && <p className="mt-1 text-xs text-violet-300">Verification required</p>}
              {a.assumptions.length > 0 && (
                <ul className="mt-2 list-disc space-y-0.5 pl-4 text-[11px] text-slate-500">
                  {a.assumptions.map((x) => <li key={x}>{x}</li>)}
                </ul>
              )}
            </div>
          ))}
        </div>

        {p.shortages.length > 0 && (
          <div>
            <p className="mb-1 text-xs uppercase text-slate-500">Shortages (exact deficit; no invented resources)</p>
            {p.shortages.map((s) => (
              <div key={s.resource_type} className="mb-2 rounded border border-red-800/60 bg-red-950/30 p-2 text-xs">
                <p className="font-medium text-red-300">{RESOURCE_LABEL[s.resource_type]}: {s.required} required, {s.available} available, deficit {s.deficit}</p>
                <p className="text-slate-400">{s.note}</p>
              </div>
            ))}
          </div>
        )}

        <div>
          <p className="mb-1 text-xs uppercase text-slate-500">Plan assumptions</p>
          <ul className="list-disc space-y-0.5 pl-4 text-xs text-slate-400">{p.assumptions.map((x) => <li key={x}>{x}</li>)}</ul>
        </div>

        <div>
          <p className="mb-1 text-xs uppercase text-slate-500">
            Reviewer findings: {blocking.length ? `${blocking.length} blocking` : "passed"}
          </p>
          {nonInfo.length ? (
            <ul className="space-y-1">
              {nonInfo.map((f, n) => <li key={n} className={`rounded border px-2 py-1 text-xs ${FINDING_STYLE[f.severity]}`}>{pretty(f.check)}: {f.message}</li>)}
            </ul>
          ) : <p className="text-xs text-emerald-300">No blocking issues or warnings.</p>}
        </div>
      </div>
    </section>
  );
}

function AgentTrace({ run }: { run: AgentRun }) {
  const [open, setOpen] = useState<Record<number, boolean>>({});
  const total = run.steps.reduce((n, s) => n + s.duration_ms, 0);
  return (
    <Panel title={`Agent trace: ${run.id}`}
      action={<span className="text-xs text-slate-400">{pretty(run.trigger)} · {run.status} · {timeAgo(run.started_at)} · {total} ms of agent time</span>}>
      <ol className="space-y-2">
        {run.steps.map((s, n) => (
          <li key={n} className="rounded-md border border-slate-800">
            <button onClick={() => setOpen((o) => ({ ...o, [n]: !o[n] }))} className="flex w-full items-center gap-3 px-3 py-2 text-left text-sm hover:bg-slate-800/40">
              {open[n] ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              <Bot size={14} className="text-sky-300" />
              <span className="w-56 font-medium">{pretty(s.agent)}</span>
              <span className="flex-1 truncate text-xs text-slate-400">{s.output_summary}</span>
              <span className="text-xs text-slate-500">{s.duration_ms} ms</span>
              <span className={`flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] ${s.llm_used ? "bg-fuchsia-600/20 text-fuchsia-300" : "bg-slate-800 text-slate-400"}`}>
                <Cpu size={10} />{s.llm_used ? "LLM used" : "no LLM"}
              </span>
              {s.error && <span className="text-xs text-red-400">error</span>}
            </button>
            {open[n] && (
              <div className="space-y-2 border-t border-slate-800 px-4 py-3 text-xs">
                <p><span className="text-slate-500">Input: </span>{s.input_summary}</p>
                <p><span className="text-slate-500">Output: </span>{s.output_summary}</p>
                {s.error && <p className="text-red-300">Error: {s.error}</p>}
                <table className="w-full">
                  <thead className="text-slate-500"><tr><th className="py-1 text-left">Tool call</th><th className="text-left">Arguments</th><th className="text-left">Result</th><th>OK</th></tr></thead>
                  <tbody className="divide-y divide-slate-800">
                    {s.tool_calls.map((t, k) => (
                      <tr key={k} className="align-top">
                        <td className="py-1 pr-2 font-mono text-sky-300">{t.name}</td>
                        <td className="pr-2 font-mono text-slate-400">{JSON.stringify(t.args)}</td>
                        <td className="pr-2 text-slate-300">{t.result_summary}</td>
                        <td className="text-center">{t.ok ? "✓" : <span className="text-amber-300" title="failed or fallback used">fallback</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </li>
        ))}
      </ol>
    </Panel>
  );
}
