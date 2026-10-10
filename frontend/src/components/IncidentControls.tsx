import { Link } from "react-router-dom";
import { RefreshCw } from "lucide-react";
import { api, USE_MOCKS } from "../api";
import { useApi } from "../api/useApi";
import { useMutation } from "../api/useMutation";
import { Button, Notice, Panel } from "./ui";
import type { Incident } from "../types";

/** Coordinator controls on Incident Details: duplicate review, verification status and reassessment. */
export default function IncidentControls({ incident: i, onChange }: { incident: Incident; onChange: () => void }) {
  const links = useApi(() => (USE_MOCKS ? Promise.resolve([]) : api.getDuplicates(i.id)));
  const m = useMutation();
  const after = () => { links.reload(); onChange(); };
  if (USE_MOCKS) return null;

  return (
    <Panel title="Coordinator controls">
      {m.error && <div className="mb-3"><Notice tone="error">{m.error}</Notice></div>}
      <div className="space-y-4 text-sm">
        <div>
          <p className="mb-1 text-xs uppercase text-slate-500">Duplicate review (linked, never deleted)</p>
          {links.data?.length ? (
            <ul className="space-y-2">
              {links.data.map((d) => {
                const other = d.incident_id === i.id ? d.candidate_id : d.incident_id;
                const b = d.breakdown;
                return (
                  <li key={d.id} className="rounded border border-slate-800 p-2">
                    <p>
                      <span className="font-mono text-xs text-slate-400">{d.id}</span>{" "}
                      {d.classification === "potential_duplicate" ? "Possible duplicate" : "Related"} of{" "}
                      <Link to={`/incidents/${other}`} className="text-sky-400 hover:underline">{other}</Link>{" "}
                      · score {d.score.toFixed(2)} · <span className="text-slate-300">{d.status}</span>
                    </p>
                    <p className="mt-1 text-xs text-slate-500">
                      distance {b.distance_m === null ? "unknown" : `${Math.round(b.distance_m)} m`} ({b.distance_score.toFixed(2)}) ·
                      time {Math.round(b.time_diff_min)} min ({b.time_score.toFixed(2)}) · type {b.type_match ? "same" : "different"} ·
                      text {b.text_similarity.toFixed(2)}
                    </p>
                    {d.status === "pending" && (
                      <div className="mt-2 flex gap-2">
                        <Button tone="primary" busy={m.busy === `c-${d.id}`} onClick={() => m.run(`c-${d.id}`, () => api.reviewDuplicate(d.id, "confirm"), after)}>Confirm duplicate</Button>
                        <Button busy={m.busy === `r-${d.id}`} onClick={() => m.run(`r-${d.id}`, () => api.reviewDuplicate(d.id, "reject"), after)}>Not a duplicate</Button>
                      </div>
                    )}
                    {d.reviewed_by && <p className="mt-1 text-xs text-slate-500">Reviewed by {d.reviewed_by}</p>}
                  </li>
                );
              })}
            </ul>
          ) : <p className="text-slate-500">No linked reports.</p>}
        </div>

        <div>
          <p className="mb-1 text-xs uppercase text-slate-500">Verification ({i.verification_status})</p>
          <div className="flex flex-wrap gap-2">
            {(["verified", "unverified", "pending"] as const).filter((v) => v !== i.verification_status).map((v) => (
              <Button key={v} busy={m.busy === v} onClick={() => m.run(v, () => api.patchIncident(i.id, { verification_status: v }), onChange)}>
                Mark {v}
              </Button>
            ))}
          </div>
        </div>

        <Button tone="primary" busy={m.busy === "run"} onClick={() => m.run("run", api.runAssessment, onChange)}>
          <RefreshCw size={12} className="mr-1 inline" />Reassess now
        </Button>
      </div>
    </Panel>
  );
}
