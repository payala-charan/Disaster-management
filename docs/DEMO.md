# Live demo script (about 15 minutes)

Follows the required flow: **Incident Report → Information Extraction → Duplicate Detection → Severity Assessment →
Weather Analysis → Geospatial Analysis → Resource Availability → Resource Allocation → Human Approval → Deployment
Tracking → Changed Condition → Dynamic Replanning → Situation Report.**

## Before the demo
```bash
docker compose up -d
cd backend && source .venv/bin/activate
# .env: WEATHER_MODE=simulation (scripted weather), ROUTING_MODE=live (OSRM) or simulation, LLM_PROVIDER=gemini + GEMINI_API_KEY (optional)
python -m app.db.seed --reset
uvicorn app.main:app --port 8000
cd ../frontend && npm run dev          # http://localhost:5173
```
Open the app and type your name in **"Acting as"** (bottom left). It is recorded in every audit event. Check the green **Live** dot.
Say up front: *"This is decision support. Nothing is dispatched without a coordinator, and every fact is labelled known, reported or inferred."*

## 1. Baseline (1 min)
**Simulation → TC-03**. Then **Dashboard**: 3 active incidents (INC-004 is a linked duplicate), INC-003 **Critical 89**, a boat
shortage of exactly 12, and alerts in the right column.

## 2. Incident report → extraction (2 min)
**Simulation → Inject a source report** → *emergency call* (sample text about River Street, Zone D, 25 people, a leg injury) → **Send to Intake agent**.
A toast/refresh follows. **Incident Center** shows a new Zone D flood incident. Open it:
- people **25 (reported)**, assistance *evacuation, medical check*, infrastructure *road blocked*;
- location **inferred** (zone centre, "confirm exact site");
- **Priority assessment** table: six factors, points and provenance badges; "Score computed by deterministic rules, not by the AI model".

## 3. Duplicate detection (1 min)
Inject the *citizen report* sample ("water flooding river street zone D!! … pls send boat").
Open the new incident → **Coordinator controls → Duplicate review**: *Possible duplicate*, score ≈ 0.9 with the full breakdown
(distance, time, type, text). People count stays **Unknown**. Click **Confirm duplicate**. Nothing is deleted.

## 4. Severity, weather, geospatial, resources (2 min)
- **Route & Access**: Zone C is *Inaccessible by road (known closure)*. The Riverside Link Road is shown separately as **AI-inferred, unconfirmed**. Travel times are labelled *routed (OSRM)* or *estimated*.
- **Resource Center**: status counts, assignments, expected availability; only valid status changes are offered.
- **Shelters**: capacity − occupancy, threshold marker at 85%.

## 5. Allocation → reviewer → agent trace (2 min)
**Response Planning**: Plans **A Priority-first**, **B Coverage** (recommended), **C Verified access**:
- per-resource reasons and unmet needs;
- the exact shortage with an escalation note ("no invented resources");
- "Ambulances cannot be sent by road: known closure of RD-C";
- the boat assumption ("verification required");
- reviewer findings.

Expand the **agent trace**: 8 agents, durations, tool calls (`osrm.table`, `postgis.nearest_shelters`, `priority.assess`, `allocation.generate_plans`, `llm.*`) and LLM used yes/no.

## 6. Human approval (2 min)
**Approval Center**: each card has the rationale, the assumptions to check, **inputs used with provenance**, and a decision deadline.
- **Modify resources** on the Zone C card and pick an *ambulance*. The result is **409**: "cannot reach Zone C by road (known closure of RD-C)".
- **Approve** the Zone C action with a comment. Its resources become **Reserved** (Resource Center).
- Try to approve another action that uses the same boat (modify it first). You get **409** naming the boat; that action is superseded and a re-plan runs (TC-08).

## 7. Deployment tracking (1 min)
**Action Tracker**: Pending → Approved → **Start** (deployed) → **Complete** (returning). Add a remark and open the audit trail.

## 8. Changed condition → dynamic replanning (2 min)
**Simulation → Change a road → RD-B closed**. You see:
- a critical **route_blocked** toast;
- **Dashboard → Recently changed conditions** shows RD-B open → closed (known);
- pending actions become **Superseded**, and approved ones get "coordinator review required" (Action Tracker → audit trail);
- **Response Planning** shows a new run with trigger *road_change* and an updated recommendation.

Optional: **Worsen weather → Zone C** gives a weather toast, and INC-003 goes 89 → 91. **Fill a shelter → Shelter A 86%** gives exactly one capacity alert.

## 9. Situation report (1 min)
**Situation Reports → Generate report → Download PDF**: 15 sections (incident summary … data uncertainties, assessment
timestamp) with the safety disclaimer on every page. The narrative is AI-written but grounding-checked, or a template when the LLM is off.

## Fallback talking points
- **No LLM key / outage:** everything still works. The trace shows `fallback (…)` and confidence `low`.
- **Offline frontend:** `VITE_USE_MOCKS=true npm run dev` shows the original mock data.
- **All eight test cases:** `python -m app.tools.run_scenarios` writes [TEST_REPORT.md](TEST_REPORT.md).
