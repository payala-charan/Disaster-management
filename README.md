# Agentic AI Disaster Response & Emergency Resource Coordination (POC)

> **Safety statement.** This is a decision-support prototype. AI agents analyse reports and *recommend*;
> they never dispatch anything. Every recommendation becomes a **pending** action that an authorized
> coordinator must approve, and resources are reserved only at that moment. Information is labelled
> **known**, **reported** or **inferred**, and missing facts are shown as *Verification Required* rather
> than guessed. Do not use this system for real emergencies without independent validation.

---

## Contents
1. [Overview](#1-overview) · 2. [Problem statement](#2-problem-statement) · 3. [System architecture](#3-system-architecture)
4. [Multi-agent architecture](#4-multi-agent-architecture) · 5. [Methodology](#5-methodology) · 6. [Human-in-the-loop](#6-human-in-the-loop)
7. [Database design](#7-database-design) · 8. [API](#8-api) · 9. [Setup and local execution](#9-setup-and-local-execution)
10. [Deployment](#10-deployment) · 11. [Testing](#11-testing) · 12. [Limitations and future work](#12-limitations-and-future-work)

Further documents: [Architecture diagrams](docs/architecture.md) · [Methodology (generated from code)](docs/METHODOLOGY.md) ·
[Test report TC-01…TC-08](docs/TEST_REPORT.md) · [Demo script](docs/DEMO.md) · [Agent graph](docs/agent_graph.md) ·
[Agent prompts](backend/app/agents/prompts/) · [Simulation scenarios](backend/data/scenarios/README.md)

---

## 1. Overview

During a flood (or landslide, urban fire, …) a coordination centre receives reports in many formats: phone calls,
field-team updates, sensor events, government alerts and citizen messages. This system:

- **normalizes** every report into a common incident format (LLM extraction with a deterministic fallback),
- **links duplicates** for human review (never merges or deletes),
- **scores priority** with a transparent 6-factor rule table (0–100),
- **analyses weather** (Open-Meteo) and **road access** (road registry + OSRM + PostGIS),
- **tracks resources and shelters** with explicit state machines and derived capacities,
- **builds three alternative allocation plans**, reports exact shortages, and has them checked by a **reviewer agent**,
- turns the recommended plan into **pending actions** for a coordinator, and **re-plans** automatically when conditions change,
- produces **PDF situation reports**, live **alerts** over WebSocket, and a full **agent trace** for every run.

| Layer | Technology |
|---|---|
| Frontend | React 19, TypeScript (strict), Vite, Tailwind v4, react-leaflet (OpenStreetMap tiles) |
| Backend | FastAPI, Pydantic v2, SQLAlchemy 2 (typed), GeoAlchemy2 |
| Agents | LangGraph 1.x (8 agents, parallel fan-out, conditional critic loop, checkpointer) |
| Data | PostgreSQL 17 + PostGIS 3.5 (Docker) |
| External | Google Gemini (default) or OpenAI, both with structured outputs; Open-Meteo (weather), OSRM (routing), optional Nominatim |
| Reports / tests | ReportLab, pytest (368 tests), headless scenario runner |

## 2. Problem statement

Emergency coordinators must decide quickly with incomplete, duplicated and sometimes contradictory information,
while weather, roads and resource availability keep changing. Generic chat assistants are unsafe here: they
can invent resources or routes, hide uncertainty, and blur the line between a confirmed road closure and a guess.

This prototype shows how AI can **reduce coordinator workload** (reading, extracting, summarising, explaining) while
every **decision-relevant number is computed by deterministic, unit-tested rules**, every fact carries its
provenance, and **only a human can commit resources**.

## 3. System architecture

```mermaid
flowchart LR
    B[Browser: React SPA] -->|REST /api| F[FastAPI]
    B <-->|WebSocket /ws: alerts and refresh| F
    F --> S[Services: actions, replanning, operations, reports, simulation]
    S --> G[LangGraph workflow: 8 agents]
    G --> E[Deterministic engines: priority, duplicates, resources, shelters, allocation, access, replanning]
    G --> I[Integrations: OpenAI, Open-Meteo, OSRM, geocoder]
    S & G --> D[(PostgreSQL + PostGIS)]
```

```
disaster-response/
├── backend/app/
│   ├── engines/        pure deterministic rules (no DB, no network), fully unit-tested
│   ├── integrations/   llm.py (OpenAI + fallback + grounding), weather.py, routing.py, geocoding.py
│   ├── agents/         the 8 agents + prompts/*.md
│   ├── graph/          LangGraph state and workflow
│   ├── services/       approval workflow, replanning, alerts, PDF reports, simulation, WebSocket bus
│   ├── api/            FastAPI routers (thin)
│   ├── db/             ORM models, repository queries, seed loader
│   ├── models/         Pydantic API contract (mirrors frontend/src/types/index.ts)
│   └── tools/          scenario runner (TEST_REPORT.md), docs generator
├── backend/data/       seed/ (base scenario) and scenarios/ (simulation timelines)
├── frontend/src/       pages, api client, live updates, shared types
└── docs/               architecture, methodology, test report, demo script
```

**The LLM vs deterministic split** (design rule 1):

| The LLM (Gemini or OpenAI) does | Deterministic, unit-tested Python does |
|---|---|
| Extract structured fields from free-text reports | Priority scores and levels |
| Classify threat indicators (fixed vocabulary) | Duplicate scores |
| Reword explanations and situation-report narratives (grounding-checked) | Resource requirements, allocation, shortages and deficits |
| | Shelter capacity, access status, state transitions, double-allocation checks, replanning impact |

If the key is missing or a call fails or times out (10 s), a regex/keyword extractor and template text are used.
Output is marked confidence `low` and `llm_used=false` in the trace, and the demo never crashes.

## 4. Multi-agent architecture

### Agent responsibilities

| # | Agent | Responsibility | Tools it calls |
|---|---|---|---|
| 1 | **Incident Intake** | Normalizes raw reports from 5 source types; detects missing fields; geocodes; links duplicates; turns area-wide advisories into alerts instead of invented incidents | `llm.extract_incident`, `llm.classify_threat`, `geocoder.resolve`, `duplicates.find_candidates` |
| 2 | **Situation Assessment & Severity** | Requirement estimate + priority score per incident; raises critical/severity-increase alerts; keeps assessment history | `priority.assess` |
| 3 | **Weather & Environmental Intelligence** | Weather per affected zone; detects worsening vs the previous snapshot | `weather.get` (Open-Meteo or simulation) |
| 4 | **Geospatial & Accessibility** | Zone access from the road registry (known vs inferred kept apart), travel matrix, nearest shelters, isolated zones | `road_registry.list`, `osrm.table`, `postgis.nearest_shelters` |
| 5 | **Emergency Resource Management** | Current resource states, assignments, expected availability, shelter status | `resource_registry.list`, `shelter_registry.list` |
| 6 | **Resource Allocation & Response Planning** | Plans A/B/C with shortages; LLM writes the recommended plan's explanation from the plan JSON only | `allocation.generate_plans`, `llm.explain_plan` |
| 7 | **Emergency Coordination & Action** | Chooses the recommended plan that passed review; supersedes old pending actions; creates pending actions; persists alerts and changes; summary + unresolved issues | `db.persist_recommendation` |
| 8 | **Reviewer / Critic** | Deterministic checks on every plan (double allocation, live resource validity, capability, route claims, boat assumption, shortage math, priority recomputation, grounding, shelter capacity); loops back to allocation (max 2×) | `resource_registry.list` (fresh), `road_registry.list` (fresh) |

### Agent prompts
System prompts are files: [`extraction.md`](backend/app/agents/prompts/extraction.md),
[`threat_classification.md`](backend/app/agents/prompts/threat_classification.md),
[`explanation.md`](backend/app/agents/prompts/explanation.md), [`sitrep.md`](backend/app/agents/prompts/sitrep.md).
All of them require: use only the provided data, output null for unknown values, never invent resources, routes or numbers.

### Agent-to-agent communication and shared workflow state
Agents do not call each other. LangGraph passes a shared **`WorkflowState`** ([state.py](backend/app/graph/state.py)):
each agent reads the keys it needs and returns only the keys it changed. Keys written by parallel agents
(`trace`, `alerts_to_emit`, `changes`, `errors`) use an append reducer, so concurrent updates merge safely.

```
START → incident_intake → [weather ‖ geospatial ‖ resource_management] → situation_assessment
      → resource_allocation → reviewer_critic → (blocking? → resource_allocation : emergency_coordination) → END
```

Dependencies (DB session factory, LLM, weather, routing, geocoder) are passed as LangGraph **context**,
never stored in checkpoints. Every agent writes an `agent_steps` row: duration, input/output summary,
each tool call (args, result, ok) and whether the LLM was used. This trace is visible in **Response Planning**.
Diagrams: [docs/architecture.md](docs/architecture.md).

### Incident data model
`Incident` (see [types/index.ts](frontend/src/types/index.ts)): `id` (INC-001), `type`, `title`, `description`, `zone`,
`location` (`{lat,lng}` or **null when unknown**), `reported_at`, `source`, `people_affected` (**null when not reported, never guessed**),
`assistance_requested`, `infrastructure_issues`, `confidence`, `verification_status`, `duplicate_of`, `related_reports`,
`priority` (latest assessment with factors and provenance), `assigned_resource_ids`, `status`. In the database each field's
origin is stored in `field_provenance` (`known` / `reported` / `inferred`).

## 5. Methodology

Full rule text is generated from the code: **[docs/METHODOLOGY.md](docs/METHODOLOGY.md)**. Summary:

### Priority score (0–100), [engines/priority.py](backend/app/engines/priority.py)
| Factor | Max | Rule |
|---|---|---|
| Threat to life | 25 | trapped/stranded/injured/water_rescue 22 · isolated/cut_off 18 · evacuation_needed 12 · property damage only 5 · none 0 + "Confirm threat to life" |
| People affected | 20 | unknown 0 + verification · 1–9 → 5 · 10–24 → 8 · 25–49 → 14 · ≥50 → 20 |
| Access route | 15 | best **known** route: open 0 · partially blocked 8 · closed 15 · unknown / only inferred 10 + "Route verification required" |
| Weather / escalation | 15 | severe warning and ≥10 mm/h forecast 13 · heavy rain 10 · moderate 5 · none 0 · +2 if wind ≥ 50 km/h |
| Infrastructure impact | 10 | main road closed 8 · road partially flooded / power or utility loss 6 · other 4 · none 0 |
| Response gap | 15 | nothing assigned 15 · assigned below estimated requirement 8 · covered 0 |

Levels: Critical ≥ 75, High 55–74, Moderate 35–54, Low < 35. **Verification Required** overrides the level when the location
is unknown, or when a low-confidence unverified report is not corroborated by a verified duplicate. Seed results:
INC-003 **89 Critical**, INC-001 61 High, INC-002 58 High.

### Duplicate detection, [engines/duplicates.py](backend/app/engines/duplicates.py)
`score = 0.35·distance + 0.25·time + 0.15·type + 0.25·text` (distance over 500 m, time over 60 min, rapidfuzz token-set text similarity).
≥ 0.70 → *potential duplicate*, 0.50–0.70 → *related*. Links are created for **human review** (confirm/reject in Incident Details);
nothing is merged or deleted. Only potential duplicates count as corroboration, and a pending duplicate is not allocated separately.

### GIS architecture and mapping
PostGIS (SRID 4326) stores incident/resource/shelter points and road linestrings. `ST_DistanceSphere` finds the nearest shelters.
A zone gazetteer resolves named places, and an optional Nominatim lookup is rate-limited (1 req/s). The frontend map uses
react-leaflet with OpenStreetMap tiles: incidents coloured by priority, duplicates dashed, roads coloured by status with
provenance, resources by status, shelters by utilization. An incident with an unknown location is never drawn at a guessed point.

### Weather API integration, [integrations/weather.py](backend/app/integrations/weather.py)
Open-Meteo current and 6-hour hourly forecast per zone (no key), cached 10 min. Condition and a *derived* severe flag come from
documented thresholds (labelled as not an official warning). On failure the system shows the last reading (marked stale), then
labelled simulation data. `WEATHER_MODE=simulation` uses scripted scenario weather.

### Route / accessibility analysis, [engines/access.py](backend/app/engines/access.py) and [integrations/routing.py](backend/app/integrations/routing.py)
Only **known** registry roads decide a zone's access status (best of open > partially blocked > closed). Reported roads are used
only when no known road exists, and always flagged. **AI-inferred** issues never set the status and are shown separately. OSRM
gives travel times only (it knows nothing about floods); if OSRM fails the estimate is haversine × 1.3 at 30 km/h, labelled
`estimated`. No alternative route is ever invented.

### Resource state management, [engines/resources.py](backend/app/engines/resources.py)
`available → reserved → deployed → returning → available`, `reserved → available` (cancel), `any → unavailable → available`.
Anything else is rejected (HTTP 409). Reserve, deploy and return happen only through action approval, start and complete.
Requirements per incident come from documented formulas (boats = ceil(people/8), teams = 1 + floor(people/30),
ambulances = max(1, ceil(people × 0.05)), …). An unknown people count gives a minimum of 1 unit plus verification.
Fire needs are reported as *external* (no fire units in the registry).

### Allocation logic, [engines/allocation.py](backend/app/engines/allocation.py)
Only `available` resources are used, and a `used` set prevents any resource appearing twice in a plan. Road-only units are
infeasible to zones whose known routes are all closed, and boats always carry *"Water and route conditions permit boat
deployment: verification required"*. Candidates are ranked by capability, then travel time, then capacity.
**A Priority-first**, **B Coverage** (minimum response to every critical/high incident first), **C Verified-access only**.
Shortages state required, available and the exact deficit, with an escalation note. The recommended plan has the highest
priority-weighted coverage.

### Shelter calculations, [engines/shelters.py](backend/app/engines/shelters.py)
Available capacity = capacity − occupancy (never stored). Utilization ≥ `SHELTER_ALERT_THRESHOLD` (0.85) raises **one** alert,
when the threshold is crossed. At full capacity the status becomes `full`, a critical alert is raised, and evacuation incidents
are reassessed.

### Dynamic replanning, [services/replanning.py](backend/app/services/replanning.py)
Every change (road, weather, resource, shelter, incident update) goes through one path:
1. record a `condition_changes` row;
2. run the pure impact analysis;
3. **supersede** affected *pending* actions;
4. **flag** affected approved / in-progress actions for coordinator review (never change them);
5. raise alerts;
6. start a new agent run, which publishes an updated recommendation over WebSocket.

### Alert mechanism
Alert kinds: `critical_incident`, `severity_increase`, `resource_unavailable`, `route_blocked`, `shelter_capacity`,
`weather_worsening`, `action_overdue` (checked every 60 s against `due_by`: critical 30 min, high 60, moderate 120, low 240).
Alerts are stored, pushed over `/ws`, shown as toasts, and acknowledged in the Alert Center.

### Reviewer mechanism
See the critic table in [agents/critic.py](backend/app/agents/critic.py). It re-reads resources and roads **from the database**
before any plan reaches a human. If a resource changed during planning, the plans that use it are blocked, the resource is
excluded, and allocation re-runs (tested in `test_critic_routes_back_when_a_resource_changes_during_planning`).
Coordination never creates actions from a plan with blocking findings.

## 6. Human-in-the-loop

| Step | Endpoint | Effect |
|---|---|---|
| Approve | `POST /api/actions/{id}/approve` | Locks resource rows (`FOR UPDATE`, ID order), re-checks availability, creates `reserved` assignments. On conflict: **409**, the action is superseded and a re-plan runs |
| Reject | `…/reject` | Records the decision with a comment |
| Modify | `…/modify` | Swaps resources; they must exist, be free, and be able to reach the zone |
| Request reassessment | `…/reassess` | New agent run |
| Comment | `…/comment` | Audit note |
| Start / Complete | `…/start`, `…/complete` | reserved → deployed → returning (resources and assignments together) |

Every step writes an `action_events` row (actor from the `X-User` header, comment, time). Double allocation is prevented
in the engine (`used` set), by the reviewer (fresh DB check), by row locks, and by a **partial unique index** on active
assignments, which is proven by a two-thread concurrency test.

## 7. Database design

```mermaid
erDiagram
    raw_reports }o--o| incidents : "normalized into"
    incidents ||--o{ priority_assessments : "history"
    incidents ||--o{ duplicate_links : "newer report"
    incidents ||--o{ duplicate_links : "candidate"
    incidents ||--o{ actions : "recommended for"
    incidents ||--o{ resource_assignments : ""
    resources ||--o{ resource_assignments : "one active at most (partial unique index)"
    resources ||--o{ resource_status_history : ""
    actions ||--o{ resource_assignments : "created on approval"
    actions ||--o{ action_events : "audit trail"
    plans ||--o{ actions : ""
    agent_runs ||--o{ plans : ""
    agent_runs ||--o{ agent_steps : "trace"
    agent_runs ||--o{ priority_assessments : ""
    agent_runs ||--o{ situation_reports : ""
    incidents { string id PK "INC-001"  geometry geom "POINT 4326, nullable"  int people_affected "nullable"  jsonb field_provenance }
    resources { string id PK "BOAT-001"  string status  geometry geom }
    resource_assignments { int id PK  string resource_id FK  string status "reserved|deployed|returning|released" }
    road_segments { string id PK  geometry geom "LINESTRING"  string status  string provenance "known|reported|inferred" }
    shelters { string id PK  int capacity  int current_occupancy }
    weather_snapshots { int id PK  string zone  jsonb forecast_hourly  string source }
    plans { string id PK  string variant  jsonb allocations  jsonb shortages  jsonb critic_findings }
    actions { string id PK  string status  jsonb rationale  jsonb inputs_used  timestamptz due_by }
    alerts { string id PK  string kind  string severity }
    condition_changes { string id PK  string entity_id  string provenance }
```

18 tables, created by `python -m app.db.init_db` (`create_all` + `CREATE EXTENSION postgis`). Status columns have CHECK
constraints. Derived values (shelter free space, current priority, `duplicate_of`, assigned resources) are computed, never
stored. Alembic migrations are future work.

## 8. API

Interactive documentation: **`http://localhost:8000/docs`** (OpenAPI). Main groups (prefix `/api`):

| Group | Endpoints |
|---|---|
| Situation | `GET /health`, `GET /situation`, `GET /zones` |
| Incidents | `GET/POST /incidents`, `GET/PATCH /incidents/{id}`, `GET /incidents/{id}/duplicates`, `POST /duplicates/{id}/confirm\|reject`, `POST /intake/reports` |
| Runs and plans | `POST /assessments/run`, `GET /assessments`, `GET /assessments/{run_id}`, `GET /plans/latest`, `GET /plans/{id}` |
| Actions | `GET /actions`, `GET /actions/{id}`, `GET /actions/{id}/events`, `POST /actions/{id}/approve\|reject\|modify\|reassess\|comment\|start\|complete` |
| Registry | `GET /resources`, `PATCH /resources/{id}/status`, `GET /resources/{id}/history`, `GET/PATCH /shelters`, `GET/PATCH /roads`, `GET /access/{incident_id}`, `GET /weather`, `POST /weather/refresh` |
| Alerts and reports | `GET /alerts`, `POST /alerts/{id}/ack`, `POST /reports/situation`, `GET /reports`, `GET /reports/{id}`, `GET /reports/{id}/pdf` |
| Simulation | `POST /simulation/reset\|report\|road\|weather\|shelter\|resource\|incident`, `GET /simulation/scenarios`, `POST /simulation/scenario/{TC-01…TC-08}` |
| Live | `WS /ws`: `{type: alert \| situation_changed \| run_started \| run_finished \| action_updated, data}` |

The JSON contract is [`frontend/src/types/index.ts`](frontend/src/types/index.ts), mirrored field-for-field by
[`backend/app/models/schemas.py`](backend/app/models/schemas.py). `tests/test_contract.py` fails if they drift.
Identity is a demo `X-User` header recorded in the audit trail. **Real authentication is future work.**

## 9. Setup and local execution

**Prerequisites:** Python 3.12, Node 20+, Docker Desktop. On Apple Silicon the PostGIS image runs under Rosetta
(`platform: linux/amd64` in compose).

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://disaster:disaster@localhost:5434/disaster_db` | `postgres://` URLs are converted automatically |
| `LLM_PROVIDER` | `none` | `gemini` (Gemini via its OpenAI-compatible endpoint), `openai`, or `none` = deterministic fallback only |
| `GEMINI_API_KEY` | – | Gemini key (used when `LLM_PROVIDER=gemini`), **backend `.env` only** (never logged; hidden from `repr`) |
| `LLM_API_KEY` | – | OpenAI key (used when `LLM_PROVIDER=openai`; also accepted for Gemini if `GEMINI_API_KEY` is unset) |
| `LLM_MODEL` | `gemini-3.5-flash-lite` / `gpt-4o-mini` | Any model that supports structured outputs (default depends on the provider) |
| `LLM_TIMEOUT_S` | `10` | After this, the fallback is used |
| `WEATHER_MODE` | `live` | `live` = Open-Meteo; `simulation` = scenario weather (**recommended for the demo**) |
| `ROUTING_MODE` | `live` | `live` = OSRM public server; `simulation` = labelled estimates |
| `HTTP_TIMEOUT_S`, `WEATHER_CACHE_MIN` | `5`, `10` | External-call tuning |
| `GEOCODING_NOMINATIM` | `false` | Off because the demo district is fictional |
| `SHELTER_ALERT_THRESHOLD` | `0.85` | Shelter alert threshold |
| `DUPLICATE_DISTANCE_M`, `DUPLICATE_TIME_WINDOW_MIN` | `500`, `60` | Duplicate scoring windows |
| `CORS_ORIGINS` | `*` | Allowed frontend origins: `*` = any origin, or a comma-separated list (`http://localhost:5173,https://your-app.vercel.app`) or JSON list |
| `OVERDUE_CHECK_S` | `60` | Overdue-action check interval |
| `VITE_API_URL` (frontend) | `http://localhost:8000` | Backend URL (public) |
| `VITE_USE_MOCKS` (frontend) | `false` | `true` = offline demo with bundled mock data |

### Backend
```bash
docker compose up -d                         # PostGIS on localhost:5434
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                         # then set GEMINI_API_KEY (optional) and WEATHER_MODE=simulation
python -m app.db.seed --reset                # tables + seed scenario
uvicorn app.main:app --reload --port 8000    # http://localhost:8000/docs
```

### Frontend
```bash
cd frontend
npm install
cp .env.example .env.local                   # optional
npm run dev                                  # http://localhost:5173
```

Then open **Simulation → TC-03** (or **Response Planning → Run assessment**) to generate the first recommendation.

## 10. Deployment

**Backend + database on Render** ([render.yaml](render.yaml)):
1. Push the repo to GitHub and create a **Blueprint** in Render from `render.yaml`. This creates the web service and a PostgreSQL database.
2. In the dashboard set `GEMINI_API_KEY` (optional) and, to restrict browsers to your frontend, `CORS_ORIGINS` (e.g. `https://your-app.vercel.app`; default `*`).
3. The start command runs `python -m app.db.init_db` (creates PostGIS and the tables) before `uvicorn`.
4. **Seed once** from the Render shell: `python -m app.db.seed` (or `--reset` to start over).
5. If your Render Postgres plan does not allow `CREATE EXTENSION postgis`, use **Neon** or **Supabase** (both support PostGIS)
   and set `DATABASE_URL` to their connection string. `postgres://` URLs are converted automatically.
6. Switch `WEATHER_MODE` between `simulation` (scripted demo) and `live` (real Open-Meteo), and `ROUTING_MODE` likewise.

**Frontend on Vercel** ([frontend/vercel.json](frontend/vercel.json)): import the repo with root directory `frontend`, set
`VITE_API_URL` to the Render URL, and deploy. The SPA rewrite serves `index.html` for every route. Secrets live only in the
Render/Vercel dashboards. Generated PDFs are stored on the service's ephemeral disk; use object storage in production.

## 11. Testing

```bash
cd backend && source .venv/bin/activate
pytest -q                                    # the whole suite (DB tests use a separate disaster_test database)
python -m app.tools.run_scenarios            # TC-01…TC-08 against a running server → docs/TEST_REPORT.md
cd ../frontend && npx tsc --noEmit -p tsconfig.app.json && npm run build
```

| Suite | What it covers |
|---|---|
| `tests/engines/` | Every rule tier and boundary, seed expectations, the 8-vs-5 boat deficit, invalid transitions, 25 randomized no-double-allocation scenarios |
| `tests/integrations/` | LLM with a fake client (success, timeout, hallucinated counts and IDs, key never leaked), fallback extractor, grounding, Open-Meteo/OSRM/Nominatim via `httpx.MockTransport` |
| `tests/graph/` | End-to-end LangGraph runs, intake of 5 source types, critic loop-back, LLM outage, agent failure, stale-checkpoint regression |
| `tests/api/` | Every endpoint group, approval lifecycle and audit trail, **two-thread concurrent approval**, WebSocket push, PDF sections |
| `tests/scenarios/` | TC-01…TC-08 (same code as the live report) |
| `tests/db/`, `test_contract.py`, `test_seed_data.py` | DB constraints and indexes, TS ↔ Pydantic contract parity, seed and scenario integrity |

No test calls the real OpenAI API (a suite-wide guard fails any attempt). Results: [docs/TEST_REPORT.md](docs/TEST_REPORT.md).

## 12. Limitations and future work

- **Authentication and roles:** the `X-User` header is a demo identity. Real deployments need SSO, roles (coordinator vs viewer) and signed audit logs.
- **Migrations:** `create_all` is used; adopt Alembic before schema changes in production.
- **Checkpointing:** the in-memory LangGraph checkpointer is lost on restart; use `langgraph-checkpoint-postgres`. Runs are serialized by a process-level lock (single instance).
- **Routing realism:** OSRM uses normal road data. Real flood routing needs live closure feeds and water-depth models. Boat suitability is always left to human verification.
- **Rules are illustrative:** weights, thresholds and requirement formulas are documented defaults, not validated operational doctrine, and need calibration with emergency-management experts.
- **Duplicate formula:** same-type reports from different zones within an hour can still be linked as *related* (score ≈ 0.5). They are shown for review but never corroborate. A distance gate would reduce this noise.
- **Weather warnings:** severe flags are derived from thresholds, not official warning feeds (e.g. national meteorological alerts).
- **Scale:** single district and tens of incidents. Larger deployments need pagination, background workers and spatial indexing beyond the defaults.
- **Fire, medical and other agencies:** resources outside the registry (fire services) are reported as external needs only.
- **Requirements document:** `docs/REQUIREMENTS.md` was not available during the build. This README follows the section list in `docs/BUILD_PLAN.md`.
