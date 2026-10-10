# CLAUDE.md — Agentic AI Disaster Response & Emergency Resource Coordination System (POC)

Claude Code loads this file automatically. Follow it in every session.

## What this project is
A decision-support prototype in which 8 specialized AI agents ingest emergency reports, assess
severity, analyze weather and accessibility, track resources and shelters, recommend allocations,
and present plans to a human coordinator for approval. **AI never dispatches anything.**

- Full problem statement: `docs/REQUIREMENTS.md` (source of truth for requirements)
- Detailed build plan: `docs/BUILD_PLAN.md` (follow its phases in order)

## Environment
- MacBook Air M4 (arm64), 16 GB RAM, macOS 15.5. **Disk space is limited (~17 GB free)**: do not
  install heavy optional packages (no GeoPandas/GDAL, no local LLMs). Ask before adding large deps.
- Conda `base` is auto-activated in the shell. Always use the project venv:
  `cd backend && source .venv/bin/activate` (verify `which python` points into `backend/.venv`).
- Python 3.12, Node 25, Docker Desktop, Homebrew PostgreSQL 17 also installed on the host (port 5432).
- Ports: Homebrew Postgres 17 uses 5432 and Postgres.app 18 uses 5433, so the PostGIS container is
  published on **5434** (`DATABASE_URL=...@localhost:5434/disaster_db`). The official
  `postgis/postgis` image has no arm64 build; compose sets `platform: linux/amd64` (Rosetta).
- Tests that need the DB use a separate `disaster_test` database (created automatically) and are
  marked `@pytest.mark.db`; they skip if the container is down.

## Repository layout (already exists)
```
disaster-response/
├── docker-compose.yml            # postgis/postgis:17-3.5
├── backend/
│   ├── requirements.txt          # installed in .venv
│   ├── .env / .env.example       # settings (LLM key lives in .env only)
│   └── app/
│       ├── core/config.py        # pydantic-settings Settings (DONE)
│       ├── db/session.py         # SQLAlchemy engine/session/Base (DONE)
│       ├── db/models.py init_db.py seed.py geo.py   # ORM tables, create, seed loader (Phase 1)
│       ├── models/schemas.py seed_data.py           # API contract + data-file schemas (Phase 1)
│       ├── engines/        # pure deterministic rules + tests (Phase 2)
│       ├── integrations/   # OpenAI, Open-Meteo, OSRM, geocoding (Phase 3)
│       ├── agents/ graph/  # 8 agents + LangGraph workflow (Phase 4)
│       ├── services/ api/ main.py   # FastAPI app, approval workflow, replanning, PDF reports (Phase 5)
│       ├── tools/          # run_scenarios (TC-01..08 → docs/TEST_REPORT.md), gen_docs (docs/METHODOLOGY.md) (Phases 7-8)
│   ├── data/seed/ data/scenarios/   tests/
└── frontend/                     # Vite + React 19 + TS + Tailwind v4 + react-leaflet
    └── src/
        ├── types/index.ts        # THE DATA CONTRACT (snake_case) — backend must mirror it
        ├── mocks/data.ts         # Zone A/B/C flood scenario (seed source)
        ├── api/                  # real.ts (fetch per endpoint), mock.ts (VITE_USE_MOCKS), live.ts (WebSocket), useApi, useMutation
        ├── lib/format.ts         # style maps + deterministic helpers
        ├── components/ui.tsx     # Panel, StatCard, PriorityBadge, ProvenanceBadge, Row
        ├── layout/AppLayout.tsx  # sidebar + safety banner
        └── pages/                # all 13 pages incl. Approval Center, Response Planning (agent trace), Simulation (Phase 6)
```

## Non-negotiable design rules
1. **LLM vs deterministic split.** The LLM (Gemini by default, or OpenAI; see `LLM_PROVIDER`) is used only for: extracting structured fields
   from unstructured reports, classifying threat indicators, and writing human-readable explanations
   and situation-report narratives. Priority scores, shelter math, resource-requirement estimates,
   allocation, shortage/deficit, duplicate scores, state transitions and double-allocation checks are
   **pure, deterministic, unit-tested Python** in `backend/app/engines/`.
2. **Provenance on every fact:** `known` (registry/API data), `reported` (from a report, unverified),
   `inferred` (AI- or heuristic-derived). Known road closures and AI-inferred potential accessibility
   problems must never be represented as equivalent.
3. **Never invent facts.** No invented resources, helicopters, roads, routes, or people counts. Missing
   or uncertain information → explicit `"Verification Required"`. If routing data is unavailable →
   `"Route verification required"`. Unknown people count stays `null`.
4. **Human-in-the-loop.** Recommendations become `pending` actions. Only an explicit API call by a
   coordinator approves/rejects/modifies/requests reassessment. Resources are only reserved on approval.
5. **No double allocation**, enforced in the engine AND in the database (row locks + partial unique
   index on active assignments).
6. **Duplicates are linked for human review, never deleted.**
7. **Explainability:** every recommendation carries rationale, assumptions, inputs used (with provenance)
   and the agent trace that produced it.
8. **Graceful degradation:** if the LLM key is missing or a call fails, fall back to deterministic
   extraction/template text and mark output confidence `low`. If Open-Meteo/OSRM fail, use the
   documented simulation/estimate mode and label it as such. The demo must never crash because of an
   external service.
9. The API contract is `frontend/src/types/index.ts` (snake_case JSON). Pydantic schemas mirror it
   field-for-field. If a type must change, update the TS type and the Pydantic schema together.
10. Do not break existing frontend pages. Keep the amber safety banner.

## Conventions
- Backend: FastAPI, Pydantic v2, SQLAlchemy 2.x (typed `Mapped[]`), GeoAlchemy2 (SRID 4326), LangGraph,
  `openai` SDK, httpx, rapidfuzz, ReportLab, pytest. Type hints everywhere. Small, focused modules.
- IDs are human-readable: `INC-001`, `BOAT-001`, `ACT-001`, `ALT-001`, `PLAN-001`, `RUN-001`.
- Timestamps: timezone-aware UTC ISO-8601.
- Frontend: TypeScript strict (`verbatimModuleSyntax` → use `import type`), Tailwind utility classes,
  reuse `components/ui.tsx` and `lib/format.ts`. Dark ops-console styling consistent with existing pages.
- Never put secrets in the frontend. Vite `VITE_*` variables are public.

## Useful commands
- Backend dev: `cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000`
- Backend tests: `cd backend && source .venv/bin/activate && pytest -q`
- Frontend dev: `cd frontend && npm run dev` (http://localhost:5173)
- Frontend type check: `cd frontend && npx tsc --noEmit -p tsconfig.app.json`
- Database: `docker compose up -d` (from repo root)
- DB console (like `rails console`): `cd backend && python -m app.console`; raw SQL: `docker exec -it disaster-db psql -U disaster -d disaster_db`
- Scenario report: `cd backend && python -m app.tools.run_scenarios` (server running, WEATHER_MODE=simulation)
- Regenerate docs: `python -m app.tools.gen_docs` and `python -m app.graph.workflow --mermaid ../docs/agent_graph.md`

## Status
Phases 1–8 of docs/BUILD_PLAN.md are complete. docs/REQUIREMENTS.md was never provided.

## Working style (the developer is learning — this matters)
- Work **one phase at a time** from `docs/BUILD_PLAN.md`. At the end of each phase:
  1. run the relevant tests / type checks and fix failures,
  2. summarize what was built (files + purpose) and the commands the developer should run to verify,
  3. briefly explain the 2–4 most important concepts in that phase,
  4. ask: *"Phase N complete. Do you want a deeper explanation of any part, or shall I continue?"*
     and **wait** for the answer.
- Before installing any new package, say what it is for and roughly how big it is.
- Prefer clear, readable code with short docstrings over clever code. Comment the "why".
