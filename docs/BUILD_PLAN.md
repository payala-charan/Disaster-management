# BUILD_PLAN.md — Remaining Work (Phases 1–8)

Read `CLAUDE.md` and `docs/REQUIREMENTS.md` first. Build phases **in order**, stopping after each one
as described in CLAUDE.md ("Working style").

---

## Phase 1 — Database fix and data layer

### 1.1 Fix the database connection
- Diagnose the port 5433 conflict (see CLAUDE.md), get `docker compose up -d` healthy, and confirm:
  `python -c "from sqlalchemy import text; from app.db.session import engine; print(engine.connect().execute(text('SELECT PostGIS_Version()')).scalar())"`

### 1.2 ORM models (`app/db/models.py`)
Use SQLAlchemy 2 typed models + GeoAlchemy2 (`Geometry('POINT', 4326)`, `Geometry('LINESTRING', 4326)`).

| Table | Key columns |
|---|---|
| `raw_reports` | id, source, received_at, raw_text, raw_payload (JSONB), normalized_incident_id, processing_status |
| `incidents` | id (INC-xxx), type, title, description, zone, geom (Point), reported_at, source, people_affected (nullable), assistance_requested (JSONB), infrastructure_issues (JSONB), confidence, verification_status, status, supporting_info, field_provenance (JSONB: field → provenance) |
| `duplicate_links` | id, incident_id, candidate_id, score, breakdown (JSONB), status (pending/confirmed/rejected), reviewed_by, reviewed_at |
| `priority_assessments` | id, incident_id, run_id, score, level, factors (JSONB), reasons, verification_needed, assessed_at (keep history; latest = current) |
| `resources` | id (BOAT-xxx…), type, name, base_name, geom, status, capacity, capabilities (JSONB), expected_available_at |
| `resource_assignments` | id, resource_id, incident_id, action_id, status (reserved/deployed/returning/released), created_at, released_at — **partial unique index on resource_id WHERE status IN ('reserved','deployed')** |
| `resource_status_history` | resource_id, from_status, to_status, reason, changed_at |
| `shelters` | id, name, geom, capacity, current_occupancy, status (available capacity is NEVER stored) |
| `road_segments` | id, name, serves_zone, geom (LineString), status, provenance, note, updated_at |
| `weather_snapshots` | zone, condition, rainfall_mm_per_hr, wind_kph, temperature_c, forecast_summary, forecast_hourly (JSONB), severe_warning, source, fetched_at |
| `plans` | id (PLAN-xxx), run_id, variant (A/B/C), title, strategy, allocations (JSONB), shortages (JSONB), explanation, assumptions, critic_findings (JSONB), is_recommended, status, created_at |
| `actions` | id (ACT-xxx), incident_id, plan_id, resource_ids, description, priority, status, rationale, assumptions, inputs_used (JSONB), created_at, approved_at, started_at, completed_at, due_by |
| `action_events` | action_id, event (created/approved/rejected/modified/reassess_requested/comment/started/completed/superseded), actor, comment, payload, at — full audit trail |
| `alerts` | id, kind, severity, message, related_id, created_at, acknowledged |
| `condition_changes` | id, entity, field, old_value, new_value, changed_at, provenance |
| `agent_runs` | id (RUN-xxx), trigger (manual/new_report/road_change/weather_change/resource_change/reassess), started_at, finished_at, status |
| `agent_steps` | run_id, agent, started_at, duration_ms, input_summary, output_summary, tool_calls (JSONB: name, args, result_summary, ok), llm_used (bool), error |

Create tables via `Base.metadata.create_all` + `CREATE EXTENSION IF NOT EXISTS postgis` in an
`app/db/init_db.py` script (no Alembic needed for the POC; mention Alembic as future work in README).

### 1.3 Pydantic schemas (`app/models/schemas.py`)
Mirror `frontend/src/types/index.ts` exactly (snake_case), plus new types listed in Phase 6.1.
Geometry is exposed as `{lat, lng}` / list of `{lat, lng}`, never WKB.

### 1.4 Seed data (single source of truth)
- Convert the scenario in `frontend/src/mocks/data.ts` into JSON files in `backend/data/seed/`
  (incidents, resources, shelters, roads, weather). Raw incidents should have **no** precomputed
  priority — the engine computes it.
- Add a richer **simulation dataset** `backend/data/scenarios/` covering the submission requirement:
  multiple incident types (flood, landslide, urban fire), extra locations, weather change timeline,
  road condition changes, shortage scenario, severity escalation. Document each scenario file.
- `python -m app.db.seed --reset` loads seed data.

---

## Phase 2 — Deterministic engines (`app/engines/`) + unit tests

Every engine is a pure function over Pydantic objects (no DB, no network) so it is trivially testable.
Document every rule in the module docstring — the README methodology sections are generated from these.

### 2.1 `priority.py` — transparent priority score (0–100)
| Factor | Max | Rule |
|---|---|---|
| Threat to life | 25 | From threat indicators: trapped/stranded/injured/water_rescue → 22; isolated/cut_off → 18; evacuation_needed → 12; property_damage_only → 5; none/unknown → 0 and add "Confirm threat to life" to verification |
| People affected | 20 | null → 0 + verification; 1–9 → 5; 10–24 → 8; 25–49 → 14; ≥50 → 20 |
| Access route | 15 | best known route status for the zone: open 0; partially_blocked 8; closed 15; unknown/only-inferred 10 + "Route verification required" |
| Weather / escalation | 15 | severe warning AND heavy rain forecast (≥10 mm/h in next 6 h) → 13; heavy rain now → 10; moderate → 5; none → 0; +2 if wind ≥ 50 km/h (cap 15) |
| Infrastructure impact | 10 | main road closed 8; road partially flooded 6; power/utility loss 6; other reported 4; none 0 |
| Response gap | 15 | nothing assigned 15; assigned capacity < estimated need 8; adequately covered 0 |

Levels: **Critical ≥ 75, High 55–74, Moderate 35–54, Low < 35.** Level is overridden to
**`verification_required`** when: location is unknown, OR confidence is `low` and the report is
unverified and not linked to a verified incident. Each factor records `value` text and `provenance`
of its input. Output `reasons` = templated sentences from the top factors (the LLM may rephrase them
later but may not add facts). Expected results for the seed scenario: INC-003 Critical (~89),
INC-002 High, INC-001 Moderate. Write tests asserting these.

### 2.2 `duplicates.py` — duplicate detection
Score = 0.35·distance_score + 0.25·time_score + 0.15·type_match + 0.25·text_similarity
- distance_score = max(0, 1 − d/`DUPLICATE_DISTANCE_M`) using haversine
- time_score = max(0, 1 − |Δt|/`DUPLICATE_TIME_WINDOW_MIN`)
- type_match = 1 if same type else 0
- text_similarity = `rapidfuzz.fuzz.token_set_ratio`/100
- ≥ 0.70 → `potential_duplicate` (create `duplicate_links` row, status pending); 0.5–0.7 → `related`.
Return the full breakdown for display. Never delete or merge automatically.

### 2.3 `resources.py` — state machine and requirement estimation
- Allowed transitions: available→reserved, reserved→deployed, reserved→available (cancel),
  deployed→returning, returning→available, any→unavailable, unavailable→available. Anything else raises.
- `estimate_requirements(incident, access, weather)` → documented rules, e.g.:
  water rescue needed (assistance has water_rescue/evacuation AND (flood + access closed/partial OR
  "stranded")) → boats = ceil(people / boat_capacity(8)); rescue teams = 1 + floor(people/30);
  ambulances = ceil(people·0.05) if medical requested (min 1); people unknown → requirement marked
  `verification_required` with minimum 1 unit. Every number shows its formula.

### 2.4 `shelters.py`
available = capacity − occupancy (floor 0); utilization; status; `approaching_threshold` when
utilization ≥ `SHELTER_ALERT_THRESHOLD`; `can_accommodate(n)`; nearest shelter with capacity.

### 2.5 `allocation.py` — allocation and alternative plans
- Inputs: prioritized incidents, requirements, available resources (status `available` only),
  access analysis, travel estimates.
- Feasibility: ground vehicles/ambulances are infeasible to a zone whose known routes are all closed;
  boats are allowed but always attach the assumption *"Water and route conditions permit boat
  deployment — verification required"* (nominal capacity alone never establishes suitability);
  unknown access → recommendation flagged `verification_required`.
- Ranking of candidate resources: capability match → travel time (OSRM or haversine estimate) → capacity.
- A `used` set guarantees no resource appears twice within a plan.
- Generate 3 variants: **A Priority-first** (fill highest priority completely first),
  **B Coverage** (at least minimum response to every critical/high incident before topping up),
  **C Verified-access only** (allocate only where access is known; others → verification tasks).
- Output per plan: allocations (incident, resources, reason per resource), unmet requirements,
  `shortages` (required, available, deficit, incidents, escalation note — never invented resources),
  assumptions, and a deterministic `plan_score` used to mark one as recommended.

### 2.6 `replanning.py`
Given a condition change (road status, weather, resource unavailable, shelter capacity), return:
affected incidents, affected approved/in-progress actions, and which must be reassessed. Pure function.

### 2.7 Tests
`backend/tests/engines/` — cover every rule, boundary values, the seed-scenario expectations,
double-allocation prevention, the 8 vs 5 boats deficit example, and invalid state transitions.

---

## Phase 3 — Integrations (`app/integrations/`)

### 3.1 LLM client (`llm.py`) — OpenAI
- `pip install openai` (add to requirements.txt). Read `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL` from
  Settings. Model name is configurable; use a small, inexpensive current model that supports
  structured outputs. Check the installed SDK version and use its structured-output API
  (Pydantic `response_format`) for extraction.
- Interface: `extract_incident(text) -> ExtractedIncident`, `classify_threat(text) -> list[str]`,
  `explain(context: dict, style) -> str`, `sitrep_narrative(snapshot) -> str`.
- System prompts live in `app/agents/prompts/*.md` (they are a README deliverable). Prompts must say:
  use only provided data, output null for unknown values, never invent resources/routes/numbers.
- **Grounding check:** after any free-text generation, verify that every ID (INC-/BOAT-/RD-/SHL-…)
  and every number mentioned exists in the input context; if not, discard and use the template text.
- Fallback when no key / error / timeout (10 s): regex + keyword extractor and templated text,
  confidence `low`, `llm_used=false` in the trace.
- Unit tests mock the OpenAI client — tests never call the real API.

### 3.2 Weather (`weather.py`) — Open-Meteo (no key)
`GET https://api.open-meteo.com/v1/forecast` with `latitude, longitude, current=temperature_2m,
precipitation,rain,wind_speed_10m,weather_code&hourly=precipitation,wind_speed_10m&forecast_hours=6`.
Derive condition, rainfall, 6-hour outlook, and `severe_warning` from documented thresholds.
`WEATHER_MODE=simulation` reads scenario data and supports injected changes. Cache 10 min.
On failure: last snapshot or simulation, labeled with its `source`.

### 3.3 Routing (`routing.py`) — OSRM public server
`/route/v1/driving/{lng},{lat};{lng},{lat}?overview=false` for distance/duration.
**Our road registry overrides OSRM:** OSRM does not know about flood closures, so a zone whose known
routes are closed is "inaccessible by road" regardless of OSRM. On OSRM failure → haversine distance ×
1.3 at 30 km/h, labeled `estimated`. Never construct alternative routes that are not in the registry.

### 3.4 Geocoding (`geocoding.py`)
Zone gazetteer first (from seed data). Optional Nominatim lookup with a proper User-Agent and
1 req/s limit; a geocoded location is provenance `inferred`. Unknown → `verification_required`.

---

## Phase 4 — Agents and LangGraph orchestration (`app/agents/`, `app/graph/`)

### 4.1 Shared workflow state (`graph/state.py`)
TypedDict with: run_id, trigger, new_reports, incidents, duplicate_candidates, assessments, weather,
access_analysis, resources, shelter_status, requirements, plans, critic_findings, critic_iterations,
recommended_plan_id, pending_actions, alerts_to_emit, changes, errors, trace.

### 4.2 Agents (one module each; each records an `agent_steps` row with tool calls)
1. **Incident Intake** — normalize raw reports from ≥3 sources (emergency call text, field-team
   structured update, IoT sensor event, government alert, citizen report) into the common incident
   format via LLM extraction; detect missing fields; call `duplicates` engine; geocode.
2. **Situation Assessment & Severity** — gathers inputs, calls `priority` engine; LLM may only
   rephrase reasons.
3. **Weather & Environmental Intelligence** — calls weather tool per zone; flags worsening vs the
   previous snapshot (→ reassessment + alert).
4. **Geospatial & Accessibility** — road registry + OSRM + PostGIS distance queries
   (`ST_DistanceSphere`) for nearest resources and shelters; isolated-area detection; outputs known vs
   inferred access separately.
5. **Emergency Resource Management** — current resource state, active assignments, expected availability.
6. **Resource Allocation & Response Planning** — requirements + `allocation` engine → 3 plans;
   LLM writes the explanation from the plan JSON only.
7. **Emergency Coordination & Action** — consolidates everything, creates `pending` actions for the
   recommended plan, unresolved issues, alerts, and the situation summary.
8. **Reviewer / Critic** — deterministic checks: duplicate allocation across plan and DB, resource
   status/capability validity, priority recomputation matches, unsupported claims (grounding check),
   route claims match the registry, contradictions between agents, shelter overflow. If blocking
   issues → route back to Allocation (max 2 iterations), else pass with findings attached.

### 4.3 Graph (`graph/workflow.py`)
`intake → [severity, weather, geospatial] (parallel fan-out) → resource_mgmt → allocation → critic
→ (conditional: back to allocation | coordination) → END (awaiting human approval)`.
Severity must re-run after weather/geo results arrive (or run after them) so the score uses current data —
choose the simplest correct ordering and document it. Human approval happens through the API on
persisted `pending` actions; use LangGraph's checkpointer (in-memory is acceptable for the POC,
document `langgraph-checkpoint-postgres` as the production option). Export a Mermaid diagram of the
graph for the README.

### 4.4 Tests
Run the graph end-to-end on the seed scenario with the LLM mocked; assert plans, no double allocation,
critic pass, actions pending, trace recorded.

---

## Phase 5 — FastAPI application (`app/main.py`, `app/api/`, `app/services/`)

### 5.1 Endpoints (prefix `/api`)
- `GET /health`
- `GET /situation` → `SituationSnapshot` (exact frontend shape)
- Incidents: `GET /incidents`, `POST /incidents` (manual registration → triggers a run),
  `GET /incidents/{id}`, `PATCH /incidents/{id}` (verification/status/people count)
- Intake: `POST /intake/reports` `{source, text?, payload?, received_at?}` → raw report → run
- Duplicates: `GET /incidents/{id}/duplicates`, `POST /duplicates/{link_id}/confirm|reject`
- Runs: `POST /assessments/run`, `GET /assessments/{run_id}` (full agent trace), `GET /assessments`
- Resources: `GET /resources`, `PATCH /resources/{id}/status` (validated transitions, 409 on invalid)
- Shelters: `GET /shelters`, `PATCH /shelters/{id}` (occupancy/status → threshold alerts)
- Roads/access: `GET /roads`, `PATCH /roads/{id}` (status change → replanning),
  `GET /access/{incident_id}` (route analysis)
- Weather: `GET /weather`, `POST /weather/refresh`
- Plans: `GET /plans/latest`, `GET /plans/{id}`
- Actions: `GET /actions`, `POST /actions/{id}/approve|reject|modify|reassess|comment`,
  `POST /actions/{id}/start|complete`. **Approve** locks resource rows (`SELECT … FOR UPDATE`),
  verifies they are still available, creates assignments (reserved) and returns 409 + triggers replanning
  if any resource is no longer available.
- Alerts: `GET /alerts`, `POST /alerts/{id}/ack`
- Reports: `POST /reports/situation` → generates PDF, `GET /reports/{id}/pdf`, `GET /reports` (list)
- Simulation: `POST /simulation/reset`, `POST /simulation/report` (inject source report),
  `POST /simulation/road`, `POST /simulation/weather`, `POST /simulation/shelter`,
  `POST /simulation/resource`, `POST /simulation/scenario/{tc_id}` (sets up TC-01…TC-08 state)
- `WS /ws` — server pushes `{type: "alert"|"situation_changed"|"run_started"|"run_finished"|"action_updated", data}`

### 5.2 Services
- `alerts.py` — creates alerts for: critical incident, severity increase, resource unavailable,
  route blocked, shelter ≥ threshold, weather worsening, action overdue; broadcasts via WebSocket.
- `replanning.py` — on change: record `condition_changes`, identify affected deployments, mark affected
  pending actions `superseded`, alert the coordinator, trigger a new run, publish updated recommendation.
- Background task (every 60 s): overdue actions (`due_by` passed) → alert.
- `reports.py` — ReportLab PDF with all Requirement-16 sections: incident summary, affected locations,
  priority assessment, weather, accessibility, available/deployed resources, shortages, shelter status,
  recommended actions, pending approvals, completed actions, data uncertainties, assessment timestamp,
  plus the safety disclaimer.
- CORS from `CORS_ORIGINS`. Simple role header `X-User` (demo coordinator name) recorded in audit events;
  note in README that real auth is future work.

---

## Phase 6 — Frontend completion

### 6.1 Types and API switch
- Add to `src/types/index.ts`: `DuplicateLink`, `Plan` (+ `PlanAllocation`), `CriticFinding`,
  `AgentRun`, `AgentStep`, `ToolCall`, `ActionEvent`, `AccessAnalysis`, `SituationReportMeta`;
  extend `ActionStatus` with `"superseded"`.
- `src/api/index.ts`: real `fetch` calls against `import.meta.env.VITE_API_URL`
  (default `http://localhost:8000`). Keep the mock implementation behind `VITE_USE_MOCKS=true`.
- `src/api/useLiveUpdates.ts`: WebSocket hook (auto-reconnect) that triggers `reload()` on
  `situation_changed` and shows toast notifications for alerts.

### 6.2 Remaining pages (consistent styling with existing pages)
- **Resource Center** — registry table (filters by type/status), state badges, assignment, expected
  availability, status-change controls that only offer valid transitions, status history.
- **Shelter Management** — capacity/occupancy/available/utilization bars, threshold indicator,
  occupancy update form.
- **Route & Accessibility** — per-zone access analysis: open / partially accessible / closed /
  unknown, known vs AI-inferred clearly separated, travel estimates with source (OSRM vs estimated),
  "Route verification required" states, road status update control (triggers replanning).
- **Response Planning** — latest run, plans A/B/C side by side, recommended badge, allocations with
  per-resource reasons, shortages, assumptions, critic findings, and an expandable **agent trace**
  (agent → tool calls → outputs → duration, LLM used yes/no). "Run assessment" button.
- **Approval Center** — pending actions with rationale, assumptions, inputs used (with provenance);
  Approve / Reject / Modify (change resources from valid available ones) / Request reassessment /
  Comment; audit trail per action.
- **Action Tracker** — Pending → Approved → In Progress → Completed timeline, start/complete
  controls, overdue highlighting, remarks.
- **Alert Center** — live list via WebSocket, severity filters, acknowledge.
- **Situation Reports** — generate, preview key sections, download PDF, list previous reports.
- **Simulation panel** (collapsible, e.g. on Dashboard or its own `/simulation` route) — buttons to
  inject source reports, close a road, worsen weather, fill a shelter, make a resource unavailable,
  and load TC-01…TC-08 scenarios. This drives the live demo.
- Incident Details: add duplicate confirm/reject buttons and "Reassess" button.
- Run `npx tsc --noEmit -p tsconfig.app.json` and `npm run build` with zero errors.

---

## Phase 7 — Test scenarios (TC-01…TC-08)

Implement as pytest integration tests (`backend/tests/scenarios/`) using the simulation endpoints with
the LLM mocked, plus a script `python -m app.tools.run_scenarios` that runs them against the live API
and writes `docs/TEST_REPORT.md`. For each TC document: Input, Initial system state, Expected output,
Actual output, Agents involved, Tool/API calls (from the agent trace), Final state, Pass/Fail.

| TC | Scenario | Expected |
|---|---|---|
| TC-01 | New flood incident reported | Incident created, normalized, assessed with factor breakdown |
| TC-02 | Same incident from two sources | Duplicate link created (pending review), breakdown shown |
| TC-03 | Critical incident with available resources | Allocation recommendation generated, pending approval |
| TC-04 | Required resources exceed availability | Shortage with exact deficit + escalation note, no invented resources |
| TC-05 | Assigned road becomes blocked | Change detected, affected actions superseded, alert, new plan |
| TC-06 | Shelter approaches capacity | Capacity alert at threshold |
| TC-07 | Weather forecast worsens | Relevant incidents reassessed, scores/levels updated, alert |
| TC-08 | Same resource considered for two incidents | Double allocation prevented (engine + DB 409) |

---

## Phase 8 — Documentation and deployment

### 8.1 README.md (all sections required by `docs/REQUIREMENTS.md`)
Overview, problem statement, system architecture, multi-agent architecture, agent responsibilities,
agent prompts (link `app/agents/prompts/`), agent-to-agent communication, shared workflow state,
incident data model, priority methodology (table above), duplicate methodology, GIS architecture,
mapping integration, weather API integration, allocation logic, resource state management, shelter
calculations, route/accessibility analysis, dynamic replanning, alert mechanism, human-approval
workflow, reviewer mechanism, database design (Mermaid ERD), API documentation (link `/docs`),
environment variables, backend setup, frontend setup, local execution, deployment, testing methodology,
safety statement, limitations and future work.

### 8.2 Architecture diagram
`docs/architecture.md` with Mermaid diagrams: the required chain
(Emergency Sources → Incident Intake → Situation Assessment → Weather Intelligence → GIS Analysis →
Resource Management → Allocation Engine → Coordination Agent → Human Approval → Action Tracking)
with agent-to-agent communication visible, plus the LangGraph graph and the replanning loop.

### 8.3 Deployment
- `render.yaml`: Python web service (`uvicorn app.main:app --host 0.0.0.0 --port $PORT`) + Render
  PostgreSQL with `CREATE EXTENSION postgis` (if unavailable on the plan, document Neon or Supabase,
  which support PostGIS). Convert `postgres://` URLs to `postgresql+psycopg://` in Settings.
- `frontend/vercel.json` with SPA rewrite to `/index.html`; `VITE_API_URL` set to the Render URL.
- Env vars documented in `.env.example` files; secrets only in Render/Vercel dashboards.
- Deployment guide in README, including seeding the production DB and switching
  `WEATHER_MODE`/`ROUTING_MODE`.

### 8.4 Demo script
`docs/DEMO.md`: the step-by-step live demo following the required flow:
Incident Report → Information Extraction → Duplicate Detection → Severity Assessment → Weather Analysis
→ Geospatial Analysis → Resource Availability → Resource Allocation → Human Approval → Deployment
Tracking → Changed Condition → Dynamic Replanning → Situation Report.
