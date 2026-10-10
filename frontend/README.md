# Frontend: Disaster Response Coordination Console

React 19 + TypeScript (strict) + Vite + Tailwind v4 + react-leaflet. See the [project README](../README.md) for the full system.

```bash
npm install
npm run dev                                   # http://localhost:5173 (backend expected at VITE_API_URL)
npx tsc --noEmit -p tsconfig.app.json         # type check
npm run build                                 # production build (dist/)
```

| Path | Purpose |
|---|---|
| `src/types/index.ts` | **The API contract** (snake_case). Mirrored by `backend/app/models/schemas.py`; a backend test fails if they drift |
| `src/api/real.ts` | One function per backend endpoint (`fetch`, sends `X-User` for the audit trail) |
| `src/api/mock.ts` | Offline mock data (`VITE_USE_MOCKS=true`) |
| `src/api/live.ts` | WebSocket `/ws` with auto-reconnect: refreshes all pages and shows alert toasts |
| `src/api/useApi.ts`, `useMutation.ts` | Data loading (live-refreshing) and mutations (busy / 409 messages) |
| `src/pages/` | Dashboard, Incident Center/Details, Live Map, Resource Center, Shelters, Route & Access, Response Planning (plans + agent trace), Approval Center, Action Tracker, Alert Center, Situation Reports, Simulation |

Environment (`.env.local`, public at build time): `VITE_API_URL` (default `http://localhost:8000`), `VITE_USE_MOCKS`.
Deployment: Vercel with `vercel.json` (SPA rewrite).
