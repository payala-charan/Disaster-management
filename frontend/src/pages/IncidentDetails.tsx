import { Link, useParams } from "react-router-dom";
import { ArrowLeft, CloudRain, Info } from "lucide-react";
import { api } from "../api";
import { useApi } from "../api/useApi";
import { Panel, PriorityBadge, ProvenanceBadge, Row } from "../components/ui";
import IncidentControls from "../components/IncidentControls";
import { RESOURCE_LABEL, ROAD_STYLE, timeAgo } from "../lib/format";

const pretty = (s: string) => s.replace(/_/g, " ");

export default function IncidentDetails() {
  const { id } = useParams();
  const { data, error, loading, reload } = useApi(api.getSituation);

  if (error) return <p className="text-red-400">Failed to load incident: {error}</p>;
  if (loading || !data) return <p className="text-slate-400">Loading incident…</p>;

  const i = data.incidents.find((x) => x.id === id);
  if (!i) {
    return (
      <div>
        <p className="text-slate-300">Incident {id} not found.</p>
        <Link to="/incidents" className="text-sm text-sky-400 hover:underline">Back to Incident Center</Link>
      </div>
    );
  }

  const original = i.duplicate_of ? data.incidents.find((x) => x.id === i.duplicate_of) : undefined;
  const linkedReports = data.incidents.filter((x) => x.duplicate_of === i.id);
  const assigned = data.resources.filter((r) => i.assigned_resource_ids.includes(r.id));
  const roads = data.roads.filter((r) => r.serves_zone === i.zone);
  const wx = data.weather.find((w) => w.zone === i.zone);
  const actions = data.actions.filter((a) => a.incident_id === i.id);
  const p = i.priority;

  return (
    <div className="space-y-6">
      <Link to="/incidents" className="flex items-center gap-1 text-sm text-slate-400 hover:text-slate-200">
        <ArrowLeft size={14} /> Incident Center
      </Link>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="font-mono text-sm text-slate-400">{i.id}</p>
          <h2 className="text-2xl font-semibold">{i.title}</h2>
          <p className="text-sm text-slate-400">{pretty(i.type)} · {i.zone} · reported {timeAgo(i.reported_at)} via {pretty(i.source)}</p>
        </div>
        <div className="flex items-center gap-3">
          {p && <span className="font-mono text-3xl font-semibold">{p.score}<span className="text-base text-slate-500">/100</span></span>}
          <PriorityBadge level={p?.level} />
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* ---------- Left: report + assessment ---------- */}
        <div className="space-y-6 lg:col-span-2">
          <Panel title="Report">
            <p className="text-sm leading-relaxed text-slate-200">{i.description}</p>
            {i.supporting_info && <p className="mt-3 text-sm text-slate-400"><span className="text-slate-500">Supporting info: </span>{i.supporting_info}</p>}
            <div className="mt-4 flex flex-wrap gap-2">
              {i.assistance_requested.map((a) => (
                <span key={a} className="rounded-full bg-sky-600/20 px-3 py-1 text-xs text-sky-200">{pretty(a)}</span>
              ))}
              {i.infrastructure_issues.map((x) => (
                <span key={x} className="rounded-full bg-amber-600/20 px-3 py-1 text-xs text-amber-200">{pretty(x)}</span>
              ))}
            </div>
          </Panel>

          <Panel title="Priority assessment">
            {!p ? (
              <p className="text-sm text-slate-400">
                Not yet assessed. The Situation Assessment agent will compute a score using the documented
                deterministic rules when the agent pipeline runs.
              </p>
            ) : (
              <>
                <table className="w-full text-sm">
                  <thead className="text-xs uppercase text-slate-500">
                    <tr className="border-b border-slate-800">
                      <th className="py-2 text-left">Factor</th>
                      <th className="py-2 text-left">Input</th>
                      <th className="py-2 text-left">Source</th>
                      <th className="w-40 py-2 text-right">Points</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800">
                    {p.factors.map((f) => (
                      <tr key={f.name}>
                        <td className="py-2 pr-3 font-medium">{f.name}</td>
                        <td className="py-2 pr-3 text-slate-300">{f.value}</td>
                        <td className="py-2 pr-3"><ProvenanceBadge value={f.provenance} /></td>
                        <td className="py-2">
                          <div className="flex items-center justify-end gap-2">
                            <div className="h-1.5 w-20 rounded bg-slate-800">
                              <div className="h-1.5 rounded bg-red-400" style={{ width: `${(f.points / f.max_points) * 100}%` }} />
                            </div>
                            <span className="w-12 text-right font-mono text-xs">{f.points}/{f.max_points}</span>
                          </div>
                        </td>
                      </tr>
                    ))}
                    <tr className="font-semibold">
                      <td className="py-2" colSpan={3}>Total</td>
                      <td className="py-2 text-right font-mono">{p.score}/100</td>
                    </tr>
                  </tbody>
                </table>
                <p className="mt-3 flex items-start gap-2 text-xs text-slate-500">
                  <Info size={14} className="mt-0.5 shrink-0" />
                  Score computed by deterministic rules, not by the AI model. Levels: Critical ≥ 75 · High 55–74 · Moderate 35–54 · Low &lt; 35.
                  Assessed {timeAgo(p.assessed_at)}.
                </p>

                <div className="mt-5 grid gap-4 md:grid-cols-2">
                  <div>
                    <p className="mb-2 text-xs uppercase text-slate-500">Reasons</p>
                    <ul className="list-disc space-y-1 pl-5 text-sm text-slate-200">
                      {p.reasons.map((r) => <li key={r}>{r}</li>)}
                    </ul>
                  </div>
                  <div>
                    <p className="mb-2 text-xs uppercase text-slate-500">Verification required</p>
                    {p.verification_needed.length ? (
                      <ul className="space-y-1 text-sm">
                        {p.verification_needed.map((v) => (
                          <li key={v} className="rounded border border-violet-700/50 bg-violet-950/40 px-2 py-1 text-violet-200">{v}</li>
                        ))}
                      </ul>
                    ) : <p className="text-sm text-slate-500">Nothing outstanding.</p>}
                  </div>
                </div>
              </>
            )}
          </Panel>

          <Panel title="Response actions">
            {actions.length ? (
              <ul className="divide-y divide-slate-800">
                {actions.map((a) => (
                  <li key={a.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                    <span><span className="font-mono text-xs text-slate-400">{a.id}</span> {a.description}</span>
                    <span className="rounded bg-slate-800 px-2 py-0.5 text-xs">{pretty(a.status)}</span>
                  </li>
                ))}
              </ul>
            ) : <p className="text-sm text-slate-500">No actions yet. Recommendations appear in Response Planning.</p>}
          </Panel>
        </div>

        {/* ---------- Right: context ---------- */}
        <div className="space-y-6">
          <IncidentControls incident={i} onChange={reload} />
          <Panel title="Status">
            <div className="divide-y divide-slate-800">
              <Row label="People affected">{i.people_affected ?? <span className="text-violet-300">Unknown: verification required</span>}</Row>
              <Row label="Confidence">{i.confidence}</Row>
              <Row label="Verification">{i.verification_status}</Row>
              <Row label="Status">{i.status}</Row>
              <Row label="Coordinates">
                {i.location
                  ? `${i.location.lat.toFixed(4)}, ${i.location.lng.toFixed(4)}`
                  : <span className="text-violet-300">Unknown: verification required</span>}
              </Row>
            </div>
          </Panel>

          {(original || linkedReports.length > 0) && (
            <Panel title="Duplicate review">
              {original && (
                <p className="text-sm text-violet-200">
                  Possible duplicate of{" "}
                  <Link to={`/incidents/${original.id}`} className="text-sky-400 hover:underline">{original.id}</Link>{" "}
                  ({original.title}). Linked for human review, not deleted.
                </p>
              )}
              {linkedReports.length > 0 && (
                <ul className="space-y-1 text-sm">
                  <li className="text-slate-400">Related reports linked to this incident:</li>
                  {linkedReports.map((x) => (
                    <li key={x.id}>
                      <Link to={`/incidents/${x.id}`} className="text-sky-400 hover:underline">{x.id}</Link>{" "}
                      <span className="text-slate-400">via {pretty(x.source)}, confidence {x.confidence}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          )}

          <Panel title="Zone weather">
            {wx ? (
              <div className="space-y-1 text-sm">
                <p className="flex items-center gap-2"><CloudRain size={16} className="text-sky-300" /> {wx.condition}</p>
                <p className="text-slate-400">{wx.rainfall_mm_per_hr} mm/h · wind {wx.wind_kph} km/h · {wx.temperature_c}°C</p>
                <p className="text-slate-300">{wx.forecast_summary}</p>
                {wx.severe_warning && <p className="text-amber-300">Severe weather warning active</p>}
                <p className="text-xs text-slate-500">Source: {wx.source} · {timeAgo(wx.fetched_at)}</p>
              </div>
            ) : <p className="text-sm text-violet-300">No weather data for this zone: verification required.</p>}
          </Panel>

          <Panel title="Access routes">
            {roads.length ? (
              <ul className="space-y-3 text-sm">
                {roads.map((r) => (
                  <li key={r.id}>
                    <div className="flex items-center justify-between gap-2">
                      <span>{r.name}</span>
                      <ProvenanceBadge value={r.provenance} />
                    </div>
                    <p className={`text-xs ${ROAD_STYLE[r.status].cls}`}>{ROAD_STYLE[r.status].label}</p>
                  </li>
                ))}
              </ul>
            ) : <p className="text-sm text-violet-300">No route data for this location: route verification required.</p>}
          </Panel>

          <Panel title="Assigned resources">
            {assigned.length ? (
              <ul className="space-y-2 text-sm">
                {assigned.map((r) => (
                  <li key={r.id} className="flex justify-between">
                    <span><span className="font-mono text-xs text-slate-400">{r.id}</span> {RESOURCE_LABEL[r.type]}</span>
                    <span className="text-slate-400">{r.status}</span>
                  </li>
                ))}
              </ul>
            ) : <p className="text-sm text-amber-300">No resources assigned.</p>}
          </Panel>
        </div>
      </div>
    </div>
  );
}
