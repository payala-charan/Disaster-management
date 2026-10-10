import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Plus, X } from "lucide-react";
import { api } from "../api";
import { useApi } from "../api/useApi";
import { Panel, PriorityBadge } from "../components/ui";
import { PRIORITY_STYLE, timeAgo } from "../lib/format";
import type { GeoPoint, IncidentSource, IncidentType, PriorityLevel } from "../types";

const TYPES: IncidentType[] = [
  "flood", "cyclone", "earthquake", "wildfire", "landslide", "industrial_accident", "urban_emergency",
];
const SOURCES: IncidentSource[] = [
  "emergency_call", "field_team", "iot_sensor", "government_alert", "citizen_report",
];
const ASSISTANCE = [
  "evacuation", "water_rescue", "search_and_rescue", "medical_check",
  "medical_transport", "food_water", "shelter",
];

const pretty = (s: string) => s.replace(/_/g, " ");
const inputCls =
  "w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-sky-500";

/** Current local time in the format a datetime-local input expects. */
const nowLocal = () =>
  new Date(Date.now() - new Date().getTimezoneOffset() * 60_000).toISOString().slice(0, 16);

const emptyForm = () => ({
  type: "flood" as IncidentType,
  title: "",
  description: "",
  zone: "",
  lat: "",
  lng: "",
  reported_at: nowLocal(),
  source: "emergency_call" as IncidentSource,
  people: "",
  assistance: [] as string[],
  supporting_info: "",
});
type Form = ReturnType<typeof emptyForm>;
type LevelFilter = PriorityLevel | "all" | "unassessed";

export default function IncidentCenter() {
  const { data, error, loading } = useApi(api.getSituation);
  const zoneList = useApi(api.getZones);
  const ZONES: Record<string, GeoPoint> = Object.fromEntries((zoneList.data ?? []).map((z) => [z.name, z.center]));
  const navigate = useNavigate();

  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<Form>(emptyForm);
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const [query, setQuery] = useState("");
  const [level, setLevel] = useState<LevelFilter>("all");
  const [hideDup, setHideDup] = useState(false);

  const set = <K extends keyof Form>(key: K, value: Form[K]) => {
    setForm((f) => ({ ...f, [key]: value }));
    setFormError(null);
  };

  const onZone = (zone: string) => {
    const p = ZONES[zone];
    setForm((f) => ({ ...f, zone, ...(p ? { lat: String(p.lat), lng: String(p.lng) } : {}) }));
  };

  const toggleAssist = (a: string) =>
    set("assistance", form.assistance.includes(a) ? form.assistance.filter((x) => x !== a) : [...form.assistance, a]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!form.title.trim() || !form.description.trim()) {
      setFormError("Title and description are required.");
      return;
    }
    const people = form.people.trim() === "" ? null : Number(form.people);
    if (people !== null && (!Number.isInteger(people) || people < 0)) {
      setFormError("People affected must be a whole number, or left empty if unknown.");
      return;
    }
    const lat = Number(form.lat);
    const lng = Number(form.lng);
    if (!form.lat || !form.lng || !Number.isFinite(lat) || !Number.isFinite(lng) || Math.abs(lat) > 90 || Math.abs(lng) > 180) {
      setFormError("Enter valid coordinates (latitude −90 to 90, longitude −180 to 180).");
      return;
    }

    setSaving(true);
    try {
      const created = await api.createIncident({
        type: form.type,
        title: form.title.trim(),
        description: form.description.trim(),
        zone: form.zone.trim() || "Unspecified",
        location: { lat, lng },
        reported_at: new Date(form.reported_at).toISOString(),
        source: form.source,
        people_affected: people,
        assistance_requested: form.assistance,
        supporting_info: form.supporting_info.trim() || undefined,
      });
      navigate(`/incidents/${created.id}`);
    } catch (err) {
      setFormError(String(err));
    } finally {
      setSaving(false);
    }
  }

  if (error) return <p className="text-red-400">Failed to load incidents: {error}</p>;
  if (loading || !data) return <p className="text-slate-400">Loading incidents…</p>;

  const q = query.trim().toLowerCase();
  const rows = data.incidents
    .filter((i) => !hideDup || !i.duplicate_of)
    .filter((i) => level === "all" || (level === "unassessed" ? !i.priority : i.priority?.level === level))
    .filter((i) => !q || `${i.id} ${i.title} ${i.zone} ${i.description}`.toLowerCase().includes(q))
    .sort((a, b) => new Date(b.reported_at).getTime() - new Date(a.reported_at).getTime());

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h2 className="text-2xl font-semibold">Incident Center</h2>
          <p className="text-sm text-slate-400">Register and manage emergency incidents</p>
        </div>
        <button
          onClick={() => setShowForm((s) => !s)}
          className="flex items-center gap-2 rounded-md bg-red-600 px-4 py-2 text-sm font-medium hover:bg-red-500"
        >
          {showForm ? <X size={16} /> : <Plus size={16} />}
          {showForm ? "Close form" : "New incident"}
        </button>
      </div>

      {showForm && (
        <Panel title="Register new incident">
          <form onSubmit={submit} className="grid gap-4 md:grid-cols-2">
            <label className="space-y-1 text-sm">
              <span className="text-slate-400">Incident type</span>
              <select className={inputCls} value={form.type} onChange={(e) => set("type", e.target.value as IncidentType)}>
                {TYPES.map((t) => <option key={t} value={t}>{pretty(t)}</option>)}
              </select>
            </label>
            <label className="space-y-1 text-sm">
              <span className="text-slate-400">Source</span>
              <select className={inputCls} value={form.source} onChange={(e) => set("source", e.target.value as IncidentSource)}>
                {SOURCES.map((s) => <option key={s} value={s}>{pretty(s)}</option>)}
              </select>
            </label>
            <label className="space-y-1 text-sm md:col-span-2">
              <span className="text-slate-400">Title *</span>
              <input className={inputCls} value={form.title} onChange={(e) => set("title", e.target.value)} placeholder="e.g. Families trapped in flooded houses" />
            </label>
            <label className="space-y-1 text-sm md:col-span-2">
              <span className="text-slate-400">Description *</span>
              <textarea className={inputCls} rows={3} value={form.description} onChange={(e) => set("description", e.target.value)} placeholder="What is happening, as reported" />
            </label>
            <label className="space-y-1 text-sm">
              <span className="text-slate-400">Zone</span>
              <select className={inputCls} value={form.zone in ZONES ? form.zone : "Other"} onChange={(e) => onZone(e.target.value === "Other" ? "" : e.target.value)}>
                <option value="Other">Select a zone…</option>
                {Object.keys(ZONES).map((z) => <option key={z}>{z}</option>)}
              </select>
            </label>
            <div className="grid grid-cols-2 gap-2">
              <label className="space-y-1 text-sm">
                <span className="text-slate-400">Latitude</span>
                <input className={inputCls} value={form.lat} onChange={(e) => set("lat", e.target.value)} />
              </label>
              <label className="space-y-1 text-sm">
                <span className="text-slate-400">Longitude</span>
                <input className={inputCls} value={form.lng} onChange={(e) => set("lng", e.target.value)} />
              </label>
            </div>
            <label className="space-y-1 text-sm">
              <span className="text-slate-400">Reported at</span>
              <input type="datetime-local" className={inputCls} value={form.reported_at} onChange={(e) => set("reported_at", e.target.value)} />
            </label>
            <label className="space-y-1 text-sm">
              <span className="text-slate-400">People affected (leave empty if unknown)</span>
              <input type="number" min={0} className={inputCls} value={form.people} onChange={(e) => set("people", e.target.value)} />
            </label>
            <div className="space-y-1 text-sm md:col-span-2">
              <span className="text-slate-400">Requested assistance</span>
              <div className="flex flex-wrap gap-2">
                {ASSISTANCE.map((a) => (
                  <button
                    type="button" key={a} onClick={() => toggleAssist(a)}
                    className={`rounded-full px-3 py-1 text-xs ring-1 ${form.assistance.includes(a) ? "bg-sky-600/30 text-sky-200 ring-sky-500" : "text-slate-400 ring-slate-700 hover:bg-slate-800"}`}
                  >
                    {pretty(a)}
                  </button>
                ))}
              </div>
            </div>
            <label className="space-y-1 text-sm md:col-span-2">
              <span className="text-slate-400">Supporting information</span>
              <textarea className={inputCls} rows={2} value={form.supporting_info} onChange={(e) => set("supporting_info", e.target.value)} placeholder="Caller details, landmarks, photos received, etc." />
            </label>

            <div className="flex items-center gap-4 md:col-span-2">
              <button type="submit" disabled={saving} className="rounded-md bg-sky-600 px-4 py-2 text-sm font-medium hover:bg-sky-500 disabled:opacity-50">
                {saving ? "Registering…" : "Register incident"}
              </button>
              <span className="text-xs text-slate-500">An incident ID is generated automatically. Priority is assessed by the agent pipeline.</span>
            </div>
            {formError && <p className="text-sm text-red-400 md:col-span-2">{formError}</p>}
          </form>
        </Panel>
      )}

      <Panel title={`Incidents (${rows.length})`}>
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <input className={`${inputCls} max-w-xs`} placeholder="Search ID, title, zone…" value={query} onChange={(e) => setQuery(e.target.value)} />
          <select className={`${inputCls} max-w-[200px]`} value={level} onChange={(e) => setLevel(e.target.value as LevelFilter)}>
            <option value="all">All priorities</option>
            {(Object.keys(PRIORITY_STYLE) as PriorityLevel[]).map((l) => <option key={l} value={l}>{PRIORITY_STYLE[l].label}</option>)}
            <option value="unassessed">Not assessed</option>
          </select>
          <label className="flex items-center gap-2 text-sm text-slate-300">
            <input type="checkbox" checked={hideDup} onChange={() => setHideDup((h) => !h)} />
            Hide possible duplicates
          </label>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase text-slate-500">
              <tr className="border-b border-slate-800">
                <th className="py-2 pr-3">ID</th>
                <th className="py-2 pr-3">Incident</th>
                <th className="py-2 pr-3">Source</th>
                <th className="py-2 pr-3">People</th>
                <th className="py-2 pr-3">Verification</th>
                <th className="py-2 pr-3">Priority</th>
                <th className="py-2">Reported</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {rows.map((i) => (
                <tr key={i.id} className="hover:bg-slate-800/40">
                  <td className="py-3 pr-3 font-mono text-xs">
                    <Link to={`/incidents/${i.id}`} className="text-sky-400 hover:underline">{i.id}</Link>
                  </td>
                  <td className="py-3 pr-3">
                    <p className="font-medium">{i.title}</p>
                    <p className="text-xs text-slate-500">
                      {pretty(i.type)} · {i.zone}
                      {i.duplicate_of && <span className="ml-2 text-violet-300">possible duplicate of {i.duplicate_of}</span>}
                    </p>
                  </td>
                  <td className="py-3 pr-3 text-slate-300">{pretty(i.source)}</td>
                  <td className="py-3 pr-3">{i.people_affected ?? <span className="text-violet-300">Unknown</span>}</td>
                  <td className="py-3 pr-3 text-slate-300">{i.verification_status}</td>
                  <td className="py-3 pr-3">
                    <div className="flex items-center gap-2">
                      <PriorityBadge level={i.priority?.level} />
                      {i.priority && <span className="font-mono text-xs text-slate-400">{i.priority.score}</span>}
                    </div>
                  </td>
                  <td className="py-3 text-xs text-slate-400">{timeAgo(i.reported_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!rows.length && <p className="py-6 text-center text-sm text-slate-500">No incidents match the filters.</p>}
        </div>
      </Panel>
    </div>
  );
}
