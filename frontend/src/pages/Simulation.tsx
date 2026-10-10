import { useState } from "react";
import { FlaskConical, RotateCcw } from "lucide-react";
import { api } from "../api";
import type { Health, ScenarioResult } from "../api";
import { useApi } from "../api/useApi";
import { useMutation } from "../api/useMutation";
import { Button, LoadState, Notice, PageHeader, Panel, inputCls } from "../components/ui";
import { pretty } from "../lib/format";
import type { IncidentSource, RoadStatus, SituationSnapshot } from "../types";

const SOURCES: IncidentSource[] = ["emergency_call", "citizen_report", "field_team", "government_alert", "iot_sensor"];
const SAMPLE_REPORTS: Record<IncidentSource, string> = {
  emergency_call: "Caller says flood water has entered ground-floor homes on River Street in Zone D. About 25 people, including elderly residents, need help to evacuate. One woman has a leg injury.",
  citizen_report: "water flooding river street zone D!! lots of people stuck, old people need help, pls send boat",
  field_team: "Field team 3: landslide on the Hill Road slope in Zone E. 2 people trapped under debris, about 15 residents need relocation.",
  government_alert: "District authority: building fire on Market Road, Zone F. People trapped on the third floor, number unknown.",
  iot_sensor: "Water level sensor WL-C-03 in Zone C reading 2.4 m, alert threshold 1.8 m, rising.",
};

async function load(): Promise<{ health: Health; scenarios: Record<string, { title: string }>; snap: SituationSnapshot }> {
  const [health, scenarios, snap] = await Promise.all([api.health(), api.simScenarios(), api.getSituation()]);
  return { health, scenarios, snap };
}

export default function Simulation() {
  const { data, error, reload } = useApi(load);
  const [result, setResult] = useState<ScenarioResult | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const m = useMutation();
  if (!data) return <LoadState error={error} what="simulation controls" />;
  const { health, snap } = data;
  const done = (msg: string) => () => { setNote(msg); reload(); };

  return (
    <div className="space-y-6">
      <PageHeader title="Simulation panel"
        subtitle="Drives the live demo. Every control goes through the same backend services as real updates, and agent runs start automatically."
        action={<Button tone="danger" busy={m.busy === "reset"} onClick={() => m.run("reset", api.simReset, done("Database reset to the seed scenario"))}>
          <RotateCcw size={14} className="mr-1 inline" />Reset scenario</Button>} />
      <Notice>
        Backend: LLM {health.llm}{health.llm_model ? ` (${health.llm_model})` : ""} · weather {health.weather_mode} · routing {health.routing_mode} · PostGIS {health.postgis.split(" ")[0]}
        {health.weather_mode !== "simulation" && " · weather injection needs WEATHER_MODE=simulation"}
      </Notice>
      {m.error && <Notice tone="error">{m.error}</Notice>}
      {note && <Notice>{note}</Notice>}

      <Panel title="Test-case scenarios (reset + setup + one agent run)">
        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
          {Object.entries(data.scenarios).map(([tc, s]) => (
            <Button key={tc} className="py-2 text-left" busy={m.busy === tc}
              onClick={() => m.run(tc, async () => setResult(await api.simScenario(tc)), reload)}>
              <FlaskConical size={12} className="mr-1 inline" /><span className="font-mono">{tc}</span> {s.title}
            </Button>
          ))}
        </div>
        {result && (
          <div className="mt-4 rounded-md border border-slate-700 p-3 text-sm">
            <p className="font-medium">{result.test_case}: {result.title}</p>
            <p className="text-xs text-slate-400">Applied: {result.applied.join("; ") || "base seed only"}</p>
            <p className="mt-1 text-slate-300">{result.run.run_id}: {result.run.summary}</p>
          </div>
        )}
      </Panel>

      <div className="grid gap-6 lg:grid-cols-2">
        <ReportInjector busy={m.busy === "report"} onSend={(source, text) =>
          m.run("report", () => api.simReport({ source, text }), done(`Report injected (${pretty(source)}); the Intake agent is processing it`))} />

        <Panel title="Change a road">
          <RoadControl roads={snap.roads} busy={m.busy === "road"} onApply={(id, status) =>
            m.run("road", () => api.simRoad(id, { status, provenance: "known", note: "Simulation panel" }), done(`${id} set to ${status}`))} />
        </Panel>

        <Panel title="Worsen weather (simulation mode)">
          <div className="flex flex-wrap gap-2">
            {[...new Set(snap.incidents.map((i) => i.zone))].sort().map((zone) => (
              <Button key={zone} busy={m.busy === `wx-${zone}`} onClick={() => m.run(`wx-${zone}`, () => api.simWeather({
                zone, condition: "Very heavy rain", rainfall_mm_per_hr: 38, wind_kph: 55, temperature_c: 25,
                forecast_summary: "Very heavy rain and strong winds for the next 6 hours", severe_warning: true,
                forecast_hourly: [1, 2, 3, 4, 5, 6].map((hour) => ({ hour, precipitation_mm: 38 - hour * 2, wind_kph: 55 })),
              }), done(`Weather worsened in ${zone}`))}>{zone}: 38 mm/h, 55 km/h</Button>
            ))}
          </div>
        </Panel>

        <Panel title="Fill a shelter">
          <div className="flex flex-wrap gap-2">
            {snap.shelters.map((s) => (
              <Button key={s.id} busy={m.busy === s.id} onClick={() => m.run(s.id, () => api.simShelter(s.id, {
                current_occupancy: Math.min(s.capacity, Math.ceil(s.capacity * 0.86)),
              }), done(`${s.name} raised to 86% occupancy`))}>{s.name} → 86%</Button>
            ))}
          </div>
        </Panel>

        <Panel title="Make a resource unavailable">
          <div className="flex flex-wrap gap-2">
            {snap.resources.filter((r) => r.status !== "unavailable").slice(0, 12).map((r) => (
              <Button key={r.id} busy={m.busy === r.id} onClick={() => m.run(r.id, () => api.simResource(r.id, {
                status: "unavailable", reason: "Breakdown (simulation)",
              }), done(`${r.id} is now unavailable`))}><span className="font-mono">{r.id}</span> ({r.status})</Button>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  );
}

function ReportInjector({ busy, onSend }: { busy: boolean; onSend: (s: IncidentSource, text: string) => void }) {
  const [source, setSource] = useState<IncidentSource>("emergency_call");
  const [text, setText] = useState(SAMPLE_REPORTS.emergency_call);
  return (
    <Panel title="Inject a source report">
      <div className="space-y-2">
        <select className={inputCls} value={source} onChange={(e) => { const s = e.target.value as IncidentSource; setSource(s); setText(SAMPLE_REPORTS[s]); }}>
          {SOURCES.map((s) => <option key={s} value={s}>{pretty(s)}</option>)}
        </select>
        <textarea className={inputCls} rows={4} value={text} onChange={(e) => setText(e.target.value)} />
        <Button tone="primary" busy={busy} disabled={!text.trim()} onClick={() => onSend(source, text.trim())}>Send to Intake agent</Button>
      </div>
    </Panel>
  );
}

function RoadControl({ roads, busy, onApply }: { roads: SituationSnapshot["roads"]; busy: boolean; onApply: (id: string, s: RoadStatus) => void }) {
  const [id, setId] = useState(roads[0]?.id ?? "");
  const [status, setStatus] = useState<RoadStatus>("closed");
  return (
    <div className="flex flex-wrap gap-2">
      <select className={`${inputCls} w-56`} value={id} onChange={(e) => setId(e.target.value)}>
        {roads.map((r) => <option key={r.id} value={r.id}>{r.id} {r.name} ({r.status})</option>)}
      </select>
      <select className={`${inputCls} w-44`} value={status} onChange={(e) => setStatus(e.target.value as RoadStatus)}>
        <option value="closed">closed</option><option value="partially_blocked">partially blocked</option><option value="open">open</option>
      </select>
      <Button tone="primary" busy={busy} disabled={!id} onClick={() => onApply(id, status)}>Apply (known)</Button>
    </div>
  );
}
