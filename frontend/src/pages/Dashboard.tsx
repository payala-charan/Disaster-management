import { Link } from "react-router-dom";
import { CloudRain } from "lucide-react";
import { api } from "../api";
import { useApi } from "../api/useApi";
import { Panel, PriorityBadge, ProvenanceBadge, StatCard } from "../components/ui";
import {
  RESOURCE_LABEL, ROAD_STYLE, shelterAvailable, shelterUtilization, timeAgo,
} from "../lib/format";
import type { ResourceType } from "../types";

export default function Dashboard() {
  const { data, error, loading } = useApi(api.getSituation);
  if (error) return <p className="text-red-400">Failed to load situation: {error}</p>;
  if (loading || !data) return <p className="text-slate-400">Loading situation…</p>;

  // ---- Derived figures (deterministic) ----
  const active = data.incidents.filter((i) => i.status === "active" && !i.duplicate_of);
  const critical = active.filter((i) => i.priority?.level === "critical");
  const people = active.reduce((sum, i) => sum + (i.people_affected ?? 0), 0);
  const unknownCounts = active.filter((i) => i.people_affected === null).length;
  const available = data.resources.filter((r) => r.status === "available").length;
  const committed = data.resources.filter((r) => r.status === "deployed" || r.status === "reserved").length;
  const shelterFree = data.shelters.filter((s) => s.status !== "closed").reduce((sum, s) => sum + shelterAvailable(s), 0);
  const roadIssues = data.roads.filter((r) => r.status !== "open");
  const openAlerts = data.alerts.filter((a) => !a.acknowledged);
  const pending = data.actions.filter((a) => a.status === "pending").length;
  const ranked = [...active].sort((a, b) => (b.priority?.score ?? -1) - (a.priority?.score ?? -1));

  const types = Object.keys(RESOURCE_LABEL) as ResourceType[];
  const byType = types
    .map((t) => {
      const all = data.resources.filter((r) => r.type === t);
      return { t, total: all.length, available: all.filter((r) => r.status === "available").length };
    })
    .filter((x) => x.total > 0);

  const wx = data.weather[0];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="text-2xl font-semibold">Emergency Dashboard</h2>
          <p className="text-sm text-slate-400">Flood response · Example District · assessed {timeAgo(data.generated_at)}</p>
        </div>
        {wx && (
          <div className="flex items-center gap-3 rounded-lg border border-sky-800/50 bg-sky-950/40 px-4 py-2 text-sm">
            <CloudRain className="text-sky-300" size={20} />
            <span>{wx.condition} · {wx.rainfall_mm_per_hr} mm/h · {wx.forecast_summary}</span>
            <span className="text-xs text-slate-500">({wx.source})</span>
          </div>
        )}
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="Active incidents" value={active.length} hint={`${data.incidents.length - active.length} linked duplicate(s)`} />
        <StatCard label="Critical" value={critical.length} tone={critical.length ? "danger" : "good"} />
        <StatCard label="People reported affected" value={people} hint={unknownCounts ? `${unknownCounts} incident(s) with unknown count` : undefined} tone="warning" />
        <StatCard label="Pending approvals" value={pending} tone={pending ? "warning" : "default"} />
        <StatCard label="Resources available" value={available} hint={`of ${data.resources.length} total`} tone="good" />
        <StatCard label="Resources committed" value={committed} hint="reserved or deployed" />
        <StatCard label="Shelter spaces free" value={shelterFree} />
        <StatCard label="Active alerts" value={openAlerts.length} tone={openAlerts.length ? "danger" : "good"} />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Incidents by priority */}
        <div className="lg:col-span-2">
          <Panel title="Incidents by priority" action={<Link to="/incidents" className="text-xs text-sky-400 hover:underline">View all</Link>}>
            <ul className="divide-y divide-slate-800">
              {ranked.map((i) => (
                <li key={i.id}>
                  <Link to={`/incidents/${i.id}`} className="flex items-center gap-4 py-3 hover:bg-slate-800/40">
                    <span className="w-16 font-mono text-xs text-slate-400">{i.id}</span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{i.title} · {i.zone}</p>
                      <p className="text-xs text-slate-500">
                        {i.people_affected ?? "Unknown"} people · {i.assigned_resource_ids.length ? `${i.assigned_resource_ids.length} resource(s) assigned` : "No resources assigned"}
                      </p>
                    </div>
                    <span className="w-10 text-right font-mono text-sm text-slate-300">{i.priority?.score ?? "–"}</span>
                    <PriorityBadge level={i.priority?.level} />
                  </Link>
                </li>
              ))}
            </ul>
          </Panel>
        </div>

        {/* Alerts */}
        <Panel title="Active alerts" action={<Link to="/alerts" className="text-xs text-sky-400 hover:underline">Alert Center</Link>}>
          <ul className="space-y-3">
            {openAlerts.map((a) => (
              <li key={a.id} className={`rounded-md border-l-4 bg-slate-800/40 p-3 text-sm ${a.severity === "critical" ? "border-red-500" : a.severity === "warning" ? "border-amber-500" : "border-sky-500"}`}>
                <p>{a.message}</p>
                <p className="mt-1 text-xs text-slate-500">{timeAgo(a.created_at)}</p>
              </li>
            ))}
            {!openAlerts.length && <p className="text-sm text-slate-500">No active alerts.</p>}
          </ul>
        </Panel>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Resources by type */}
        <Panel title="Resource availability">
          <ul className="space-y-3">
            {byType.map(({ t, total, available }) => (
              <li key={t}>
                <div className="flex justify-between text-sm">
                  <span>{RESOURCE_LABEL[t]}</span>
                  <span className="text-slate-400">{available} / {total} available</span>
                </div>
                <div className="mt-1 h-1.5 rounded bg-slate-800">
                  <div className="h-1.5 rounded bg-emerald-500" style={{ width: `${(available / total) * 100}%` }} />
                </div>
              </li>
            ))}
          </ul>
          {data.shortages.map((s) => (
            <div key={s.resource_type} className="mt-4 rounded-md border border-red-800/60 bg-red-950/30 p-3 text-sm">
              <p className="font-medium text-red-300">
                Shortage: {RESOURCE_LABEL[s.resource_type]} deficit = {s.deficit}
              </p>
              <p className="text-xs text-slate-400">Required {s.required} · Available {s.available} · {s.incident_ids.join(", ")}</p>
              <p className="mt-1 text-xs text-slate-300">{s.note}</p>
            </div>
          ))}
        </Panel>

        {/* Shelters */}
        <Panel title="Shelter availability" action={<Link to="/shelters" className="text-xs text-sky-400 hover:underline">Manage</Link>}>
          <ul className="space-y-3">
            {data.shelters.map((s) => {
              const pct = Math.round(shelterUtilization(s) * 100);
              return (
                <li key={s.id}>
                  <div className="flex justify-between text-sm">
                    <span>{s.name}</span>
                    <span className="text-slate-400">{shelterAvailable(s)} free · {s.current_occupancy}/{s.capacity}</span>
                  </div>
                  <div className="mt-1 h-1.5 rounded bg-slate-800">
                    <div className={`h-1.5 rounded ${pct >= 85 ? "bg-red-500" : pct >= 70 ? "bg-amber-500" : "bg-sky-500"}`} style={{ width: `${pct}%` }} />
                  </div>
                </li>
              );
            })}
          </ul>
        </Panel>

        {/* Roads */}
        <Panel title="Road & accessibility issues" action={<Link to="/routes" className="text-xs text-sky-400 hover:underline">Details</Link>}>
          <ul className="space-y-3">
            {roadIssues.map((r) => (
              <li key={r.id} className="text-sm">
                <div className="flex items-center justify-between gap-2">
                  <span>{r.name}</span>
                  <ProvenanceBadge value={r.provenance} />
                </div>
                <p className={`text-xs ${ROAD_STYLE[r.status].cls}`}>{ROAD_STYLE[r.status].label}</p>
                {r.note && <p className="text-xs text-slate-500">{r.note}</p>}
              </li>
            ))}
          </ul>
        </Panel>
      </div>

      {/* Recent changes */}
      <Panel title="Recently changed conditions">
        <ul className="divide-y divide-slate-800 text-sm">
          {data.changes.map((c) => (
            <li key={c.id} className="flex flex-wrap items-center gap-3 py-2">
              <span className="w-28 text-xs text-slate-500">{timeAgo(c.changed_at)}</span>
              <span className="font-medium">{c.entity}</span>
              <span className="text-slate-400">{c.field}:</span>
              <span className="text-slate-500 line-through">{c.old_value}</span>
              <span>→ {c.new_value}</span>
              <ProvenanceBadge value={c.provenance} />
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}
