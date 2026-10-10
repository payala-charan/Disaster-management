# Test Report: TC-01 … TC-08

Generated 2026-10-08 19:01 UTC by `python -m app.tools.run_scenarios` against `http://localhost:8000`.
Backend: LLM **openai** (gpt-4o-mini), weather **simulation**, routing **live**, PostGIS 3.5.

Every test case starts from a reset seed database and drives the system only through the public HTTP API. Agents involved and tool calls are taken from the persisted agent trace (`GET /api/assessments/{run_id}`).

**Result: 8/8 passed.**

| TC | Scenario | Result | Checks |
|---|---|---|---|
| TC-01 | New flood incident reported | ✅ Pass | 6/6 |
| TC-02 | Same incident from two sources | ✅ Pass | 6/6 |
| TC-03 | Critical incident with available resources | ✅ Pass | 7/7 |
| TC-04 | Required resources exceed availability | ✅ Pass | 5/5 |
| TC-05 | Assigned road becomes blocked | ✅ Pass | 6/6 |
| TC-06 | Shelter approaches capacity | ✅ Pass | 3/3 |
| TC-07 | Weather forecast worsens | ✅ Pass | 4/4 |
| TC-08 | Same resource considered for two incidents | ✅ Pass | 6/6 |

---

## TC-01: New flood incident reported

**Result:** ✅ PASS

**Input**

Emergency call (free text): flood water in ground-floor homes on River Street, Zone D; about 25 people including elderly residents need evacuation; one woman with a leg injury; cars cannot get through.

**Initial system state**

Base seed: INC-001..INC-004 (Zone A/B/C floods; INC-004 citizen duplicate of INC-002), 23 resources (BOAT-001/TEAM-001 deployed on INC-001, AMB-006 returning, VEH-008 unavailable), RD-C known closed, RD-C2 AI-inferred, simulated heavy rain

**Expected output**

- Report normalized into a flood incident in Zone D with people_affected = 25 (reported)
- Location resolved via the zone gazetteer and marked inferred (approximate)
- Priority assessed with all six factors and their provenance
- Recommendation created only as a pending action

**Actual output**

- Setup applied: report RAW-001 (emergency_call)
- Created INC-005: type=flood, zone=Zone D, people=25, assistance=['evacuation', 'medical_check'], confidence=low
- Priority 63 (high): Threat to life 22/25 [inferred]; People affected 14/20 [reported]; Access route 8/15 [known]; Weather / escalation 0/15 [inferred]; Infrastructure impact 4/10 [inferred]; Response gap 15/15 [known]

**Checks**

| Check | Result | Detail |
|---|---|---|
| exactly one new incident in Zone D | ✅ | ['INC-005'] |
| type flood, people 25 | ✅ | ('flood', 25) |
| evacuation and medical help extracted | ✅ | ['evacuation', 'medical_check'] |
| priority has 6 factors with provenance | ✅ | 6 |
| Intake agent recorded an extraction call | ✅ |  |
| any recommendation for it is pending (not dispatched) | ✅ | 1 |

**Agents involved**

incident_intake, resource_management, weather_intelligence, geospatial_accessibility, situation_assessment, resource_allocation, reviewer_critic, emergency_coordination

<details><summary>Tool calls (from the agent trace)</summary>

- incident_intake → raw_reports.fetch_pending: 1 pending report(s)
- incident_intake → llm.extract_incident: fallback (AuthenticationError): type=flood zone=Zone D people=25 [fallback/failed]
- incident_intake → geocoder.resolve: zone_name: zone=Zone D provenance=inferred
- incident_intake → duplicates.find_candidates: none
- resource_management → resource_registry.list: 23 resources
- resource_management → shelter_registry.list: 3 shelters
- weather_intelligence → weather.get: simulation: Heavy rain, 18 mm/h, wind 32 km/h, severe=True
- weather_intelligence → weather.get: simulation: Heavy rain, 24 mm/h, wind 32 km/h, severe=True
- weather_intelligence → weather.get: no data
- geospatial_accessibility → road_registry.list: 5 road segments
- geospatial_accessibility → osrm.table: 95 routed, 0 estimated
- geospatial_accessibility → postgis.nearest_shelters: SHL-A 1341 m, SHL-C 2469 m, SHL-B 3878 m
- geospatial_accessibility → postgis.nearest_shelters: SHL-B 1630 m, SHL-A 3273 m, SHL-C 3364 m
- geospatial_accessibility → postgis.nearest_shelters: SHL-C 3888 m, SHL-A 7417 m, SHL-B 8468 m
- geospatial_accessibility → postgis.nearest_shelters: SHL-B 1544 m, SHL-A 3247 m, SHL-C 3428 m
- geospatial_accessibility → postgis.nearest_shelters: SHL-B 2754 m, SHL-A 5004 m, SHL-C 7421 m
- situation_assessment → priority.assess: 61 high
- situation_assessment → priority.assess: 58 high
- situation_assessment → priority.assess: 89 critical
- situation_assessment → priority.assess: 50 moderate
- situation_assessment → priority.assess: 63 high
- resource_allocation → allocation.generate_plans: A score 20.2; B score 33.3 (recommended); C score 20.2
- resource_allocation → llm.explain_plan: fallback (AuthenticationError): grounded [fallback/failed]
- reviewer_critic → resource_registry.list: 23 resources re-read
- reviewer_critic → road_registry.list: 5 roads
- emergency_coordination → db.persist_recommendation: 3 plans, 4 pending actions, 0 superseded, 4 alerts

</details>

<details><summary>API calls</summary>

- POST /api/simulation/scenario/TC-01 -> 200
- GET /api/assessments/RUN-001 -> 200
- GET /api/incidents -> 200
- GET /api/actions?status=pending -> 200
- GET /api/incidents -> 200

</details>

**Final state**

5 active incidents; run RUN-001: 5 active incident(s); critical: INC-003. Recommended PLAN-002 (Plan B: Coverage) with 4 action(s) pending coordinator approval. Shortages: rescue_boat deficit 16; rescue_team deficit 3; ambulance deficit 1. 10 unresolved item(s).

---

## TC-02: Same incident from two sources

**Result:** ✅ PASS

**Input**

(1) Emergency call about River Street, Zone D (25 people). (2) Ten minutes later a citizen message: 'water flooding river street zone D!! lots of people stuck, old people need help, pls send boat'.

**Initial system state**

Base seed: INC-001..INC-004 (Zone A/B/C floods; INC-004 citizen duplicate of INC-002), 23 resources (BOAT-001/TEAM-001 deployed on INC-001, AMB-006 returning, VEH-008 unavailable), RD-C known closed, RD-C2 AI-inferred, simulated heavy rain

**Expected output**

- Second report becomes its own incident (nothing deleted or merged)
- A potential_duplicate link is created with status pending and a full score breakdown
- The duplicate does not receive a separate allocation

**Actual output**

- Setup applied: report RAW-001 (emergency_call); report RAW-002 (citizen_report)
- Link: {'id': 'DUP-002', 'score': 0.935, 'classification': 'potential_duplicate', 'status': 'pending'}; breakdown {'distance_m': 0.0, 'distance_score': 1.0, 'time_diff_min': 0.0, 'time_score': 1.0, 'type_match': 1.0, 'text_similarity': 0.739}
- INC-006: people=None (vague count kept unknown), confidence=low

**Checks**

| Check | Result | Detail |
|---|---|---|
| both reports exist as incidents | ✅ | ['INC-005', 'INC-006'] |
| potential_duplicate link, pending review | ✅ | {'id': 'DUP-002', 'incident_id': 'INC-006', 'candidate_id': 'INC-005', 'score': 0.935, 'classification': 'potential_duplicate', 'breakdown': {'distance_m': 0.0, 'distance_score': 1 |
| score is the documented weighted sum | ✅ | 0.935 |
| citizen people count stays unknown | ✅ | None |
| duplicate exposed as duplicate_of | ✅ | INC-005 |
| no separate action for the duplicate | ✅ |  |

**Agents involved**

incident_intake, resource_management, weather_intelligence, geospatial_accessibility, situation_assessment, resource_allocation, reviewer_critic, emergency_coordination

<details><summary>Tool calls (from the agent trace)</summary>

- incident_intake → raw_reports.fetch_pending: 2 pending report(s)
- incident_intake → llm.extract_incident: fallback (AuthenticationError): type=flood zone=Zone D people=25 [fallback/failed]
- incident_intake → geocoder.resolve: zone_name: zone=Zone D provenance=inferred
- incident_intake → duplicates.find_candidates: none
- incident_intake → llm.extract_incident: fallback (AuthenticationError): type=flood zone=Zone D people=None [fallback/failed]
- incident_intake → duplicates.find_candidates: INC-005 potential_duplicate 0.94
- resource_management → resource_registry.list: 23 resources
- resource_management → shelter_registry.list: 3 shelters
- weather_intelligence → weather.get: simulation: Heavy rain, 18 mm/h, wind 32 km/h, severe=True
- weather_intelligence → weather.get: simulation: Heavy rain, 24 mm/h, wind 32 km/h, severe=True
- weather_intelligence → weather.get: no data
- geospatial_accessibility → road_registry.list: 5 road segments
- geospatial_accessibility → osrm.table: 114 routed, 0 estimated
- geospatial_accessibility → postgis.nearest_shelters: SHL-A 1341 m, SHL-C 2469 m, SHL-B 3878 m
- geospatial_accessibility → postgis.nearest_shelters: SHL-B 1630 m, SHL-A 3273 m, SHL-C 3364 m
- geospatial_accessibility → postgis.nearest_shelters: SHL-C 3888 m, SHL-A 7417 m, SHL-B 8468 m
- geospatial_accessibility → postgis.nearest_shelters: SHL-B 1544 m, SHL-A 3247 m, SHL-C 3428 m
- geospatial_accessibility → postgis.nearest_shelters: SHL-B 2754 m, SHL-A 5004 m, SHL-C 7421 m
- situation_assessment → priority.assess: 61 high
- situation_assessment → priority.assess: 58 high
- situation_assessment → priority.assess: 89 critical
- situation_assessment → priority.assess: 50 moderate
- situation_assessment → priority.assess: 63 high
- situation_assessment → priority.assess: 45 verification_required
- resource_allocation → allocation.generate_plans: A score 20.2; B score 33.3 (recommended); C score 20.2
- resource_allocation → llm.explain_plan: fallback (AuthenticationError): grounded [fallback/failed]
- reviewer_critic → resource_registry.list: 23 resources re-read
- reviewer_critic → road_registry.list: 5 roads
- emergency_coordination → db.persist_recommendation: 3 plans, 4 pending actions, 0 superseded, 4 alerts

</details>

<details><summary>API calls</summary>

- POST /api/simulation/scenario/TC-02 -> 200
- GET /api/assessments/RUN-001 -> 200
- GET /api/incidents -> 200
- GET /api/incidents/INC-006/duplicates -> 200
- GET /api/actions?status=pending -> 200

</details>

**Final state**

INC-006 linked to INC-005 for human review (DUP-002)

---

## TC-03: Critical incident with available resources

**Result:** ✅ PASS

**Input**

Assessment run on the base seed (INC-003: village in Zone C cut off, 75 people, main road closed).

**Initial system state**

Base seed: INC-001..INC-004 (Zone A/B/C floods; INC-004 citizen duplicate of INC-002), 23 resources (BOAT-001/TEAM-001 deployed on INC-001, AMB-006 returning, VEH-008 unavailable), RD-C known closed, RD-C2 AI-inferred, simulated heavy rain

**Expected output**

- INC-003 scored 89 → Critical with factor breakdown
- Allocation recommends available boats/teams (no road vehicles through the known closure)
- Action created as pending with rationale, assumptions and inputs used; no resource reserved

**Actual output**

- Setup applied: base seed
- INC-003 priority 89 critical
- Pending ACT-002: ['BOAT-002', 'TEAM-002', 'BOAT-004']; assumptions: ['Ambulances cannot be sent by road: known closure of Main Road to Zone C (RD-C). Alternative access: route verification required.', 'Water and route conditions permit boat deployment: verification required']
- Recommended plan: PLAN-002 Plan B: Coverage

**Checks**

| Check | Result | Detail |
|---|---|---|
| INC-003 = 89 Critical | ✅ | 89 |
| pending action for INC-003 | ✅ |  |
| only water-capable resources (boats/teams) to the closed zone | ✅ | {'rescue_team', 'rescue_boat'} |
| boat verification assumption shown | ✅ |  |
| rationale and inputs with provenance present | ✅ | 7 |
| resources NOT reserved before approval | ✅ | {'BOAT-002': 'available', 'TEAM-002': 'available', 'BOAT-004': 'available'} |
| reviewer passed the recommended plan | ✅ |  |

**Agents involved**

incident_intake, resource_management, weather_intelligence, geospatial_accessibility, situation_assessment, resource_allocation, reviewer_critic, emergency_coordination

<details><summary>Tool calls (from the agent trace)</summary>

- incident_intake → raw_reports.fetch_pending: 0 pending report(s)
- resource_management → resource_registry.list: 23 resources
- resource_management → shelter_registry.list: 3 shelters
- weather_intelligence → weather.get: simulation: Heavy rain, 18 mm/h, wind 32 km/h, severe=True
- weather_intelligence → weather.get: simulation: Heavy rain, 24 mm/h, wind 32 km/h, severe=True
- geospatial_accessibility → road_registry.list: 4 road segments
- geospatial_accessibility → osrm.table: 76 routed, 0 estimated
- geospatial_accessibility → postgis.nearest_shelters: SHL-A 1341 m, SHL-C 2469 m, SHL-B 3878 m
- geospatial_accessibility → postgis.nearest_shelters: SHL-B 1630 m, SHL-A 3273 m, SHL-C 3364 m
- geospatial_accessibility → postgis.nearest_shelters: SHL-C 3888 m, SHL-A 7417 m, SHL-B 8468 m
- geospatial_accessibility → postgis.nearest_shelters: SHL-B 1544 m, SHL-A 3247 m, SHL-C 3428 m
- situation_assessment → priority.assess: 61 high
- situation_assessment → priority.assess: 58 high
- situation_assessment → priority.assess: 89 critical
- situation_assessment → priority.assess: 50 moderate
- resource_allocation → allocation.generate_plans: A score 17.6; B score 37.9 (recommended); C score 17.6
- resource_allocation → llm.explain_plan: fallback (AuthenticationError): grounded [fallback/failed]
- reviewer_critic → resource_registry.list: 23 resources re-read
- reviewer_critic → road_registry.list: 4 roads
- emergency_coordination → db.persist_recommendation: 3 plans, 3 pending actions, 0 superseded, 4 alerts

</details>

<details><summary>API calls</summary>

- POST /api/simulation/scenario/TC-03 -> 200
- GET /api/assessments/RUN-001 -> 200
- GET /api/incidents -> 200
- GET /api/actions?status=pending -> 200
- GET /api/resources -> 200
- GET /api/plans/latest -> 200

</details>

**Final state**

4 active incident(s); critical: INC-003. Recommended PLAN-002 (Plan B: Coverage) with 3 action(s) pending coordinator approval. Shortages: rescue_boat deficit 12; rescue_team deficit 2. 7 unresolved item(s).

---

## TC-04: Required resources exceed availability

**Result:** ✅ PASS

**Input**

BOAT-004 (engine failure, back in ~3 h) and BOAT-005 (hull damage) become unavailable; INC-003 people count revised from 75 to 120 (reported).

**Initial system state**

Base seed: INC-001..INC-004 (Zone A/B/C floods; INC-004 citizen duplicate of INC-002), 23 resources (BOAT-001/TEAM-001 deployed on INC-001, AMB-006 returning, VEH-008 unavailable), RD-C known closed, RD-C2 AI-inferred, simulated heavy rain

**Expected output**

- Boat requirement computed by the engine: INC-003 ceil(120/8)=15 + INC-001 (5−1 assigned)=4 + INC-002 2 = 21
- Available boats = 2 (BOAT-002, BOAT-003) → exact deficit 19 with an escalation note
- No invented resources in any plan

**Actual output**

- Setup applied: resource BOAT-004 -> unavailable; resource BOAT-005 -> unavailable; incident INC-003 updated: ['people_affected']
- Boat shortage: {'resource_type': 'rescue_boat', 'required': 21, 'available': 2, 'deficit': 19, 'incident_ids': ['INC-003', 'INC-001', 'INC-002'], 'note': 'Deficit of 19 rescue boats. Escalate to district or state emergency operations for additional water-rescue capacity.'}
- Resources used across plans: ['BOAT-002', 'BOAT-003', 'TEAM-002', 'TEAM-003', 'TEAM-004']

**Checks**

| Check | Result | Detail |
|---|---|---|
| required = 21 boats (engine rules) | ✅ | 21 |
| available = 2, deficit = 19 | ✅ | (2, 19) |
| escalation note, no invented resource | ✅ | Deficit of 19 rescue boats. Escalate to district or state emergency operations for additional water-rescue capacity. |
| every allocated resource exists in the registry | ✅ | [] |
| BOAT-004/BOAT-005 not allocated | ✅ |  |

**Agents involved**

incident_intake, resource_management, weather_intelligence, geospatial_accessibility, situation_assessment, resource_allocation, reviewer_critic, emergency_coordination

<details><summary>Tool calls (from the agent trace)</summary>

- incident_intake → raw_reports.fetch_pending: 0 pending report(s)
- resource_management → resource_registry.list: 23 resources
- resource_management → shelter_registry.list: 3 shelters
- weather_intelligence → weather.get: simulation: Heavy rain, 18 mm/h, wind 32 km/h, severe=True
- weather_intelligence → weather.get: simulation: Heavy rain, 24 mm/h, wind 32 km/h, severe=True
- geospatial_accessibility → road_registry.list: 4 road segments
- geospatial_accessibility → osrm.table: 68 routed, 0 estimated
- geospatial_accessibility → postgis.nearest_shelters: SHL-A 1341 m, SHL-C 2469 m, SHL-B 3878 m
- geospatial_accessibility → postgis.nearest_shelters: SHL-B 1630 m, SHL-A 3273 m, SHL-C 3364 m
- geospatial_accessibility → postgis.nearest_shelters: SHL-C 3888 m, SHL-A 7417 m, SHL-B 8468 m
- geospatial_accessibility → postgis.nearest_shelters: SHL-B 1544 m, SHL-A 3247 m, SHL-C 3428 m
- situation_assessment → priority.assess: 61 high
- situation_assessment → priority.assess: 58 high
- situation_assessment → priority.assess: 89 critical
- situation_assessment → priority.assess: 50 moderate
- resource_allocation → allocation.generate_plans: A score 8.2; B score 24.3 (recommended); C score 8.2
- resource_allocation → llm.explain_plan: fallback (AuthenticationError): grounded [fallback/failed]
- reviewer_critic → resource_registry.list: 23 resources re-read
- reviewer_critic → road_registry.list: 4 roads
- emergency_coordination → db.persist_recommendation: 3 plans, 3 pending actions, 0 superseded, 4 alerts

</details>

<details><summary>API calls</summary>

- POST /api/simulation/scenario/TC-04 -> 200
- GET /api/assessments/RUN-001 -> 200
- GET /api/plans/latest -> 200
- GET /api/resources -> 200

</details>

**Final state**

Recommended PLAN-002; shortages: rescue_boat −19; rescue_team −4; ambulance −1

---

## TC-05: Assigned road becomes blocked

**Result:** ✅ PASS

**Input**

After the coordinator approves the Zone B recommendation, Main Road to Zone B (RD-B) is reported CLOSED by the traffic police (known).

**Initial system state**

Base seed: INC-001..INC-004 (Zone A/B/C floods; INC-004 citizen duplicate of INC-002), 23 resources (BOAT-001/TEAM-001 deployed on INC-001, AMB-006 returning, VEH-008 unavailable), RD-C known closed, RD-C2 AI-inferred, simulated heavy rain; one run done and INC-002's action approved (resources reserved)

**Expected output**

- Condition change recorded (RD-B open → closed, known)
- route_blocked alert (critical)
- Pending recommendations superseded; the approved action is flagged for coordinator review, not changed
- New run: Zone B access becomes inaccessible by road and a new recommendation is produced

**Actual output**

- Setup applied: approved ACT-004; road RD-B -> closed
- Change: {'id': 'CHG-007', 'entity': 'Main Road to Zone B', 'field': 'status', 'old_value': 'open', 'new_value': 'closed', 'changed_at': '2026-10-08T19:01:37.224052Z', 'provenance': 'known'}
- Alerts: ['Main Road to Zone B (RD-B) changed to CLOSED (known, reported by simulation)']
- Approved action ACT-004 events: ['created: Recommended by PLAN-002 (Plan B: Coverage); awaiting coordinator approval', 'approved: Approved to set up TC-05', 'comment: Conditions changed (Main Road to Zone B: status open -> closed): coordinator review required']
- Superseded actions: ['ACT-003', 'ACT-002']
- INC-002 access now: inaccessible_by_road

**Checks**

| Check | Result | Detail |
|---|---|---|
| condition change recorded with provenance known | ✅ | {'id': 'CHG-007', 'entity': 'Main Road to Zone B', 'field': 'status', 'old_value': 'open', 'new_value': 'closed', 'changed_at': '2026-10-08T19:01:37.224052Z', 'provenance': 'known' |
| route_blocked critical alert | ✅ | 1 |
| approved action kept and flagged for review | ✅ |  |
| earlier pending recommendations superseded | ✅ | 2 |
| Zone B now inaccessible by road | ✅ | inaccessible_by_road |
| new recommendation produced after the change | ✅ | PLAN-005 |

**Agents involved**

incident_intake, resource_management, weather_intelligence, geospatial_accessibility, situation_assessment, resource_allocation, reviewer_critic, emergency_coordination

<details><summary>Tool calls (from the agent trace)</summary>

- incident_intake → raw_reports.fetch_pending: 0 pending report(s)
- resource_management → resource_registry.list: 23 resources
- resource_management → shelter_registry.list: 3 shelters
- weather_intelligence → weather.get: simulation: Heavy rain, 18 mm/h, wind 32 km/h, severe=True
- weather_intelligence → weather.get: simulation: Heavy rain, 24 mm/h, wind 32 km/h, severe=True
- geospatial_accessibility → road_registry.list: 4 road segments
- geospatial_accessibility → osrm.table: 68 routed, 0 estimated
- geospatial_accessibility → postgis.nearest_shelters: SHL-A 1341 m, SHL-C 2469 m, SHL-B 3878 m
- geospatial_accessibility → postgis.nearest_shelters: SHL-B 1630 m, SHL-A 3273 m, SHL-C 3364 m
- geospatial_accessibility → postgis.nearest_shelters: SHL-C 3888 m, SHL-A 7417 m, SHL-B 8468 m
- geospatial_accessibility → postgis.nearest_shelters: SHL-B 1544 m, SHL-A 3247 m, SHL-C 3428 m
- situation_assessment → priority.assess: 61 high
- situation_assessment → priority.assess: 66 high
- situation_assessment → priority.assess: 89 critical
- situation_assessment → priority.assess: 65 high
- resource_allocation → allocation.generate_plans: A score 12.1; B score 46.7 (recommended); C score 12.1
- resource_allocation → llm.explain_plan: fallback (AuthenticationError): grounded [fallback/failed]
- reviewer_critic → resource_registry.list: 23 resources re-read
- reviewer_critic → road_registry.list: 4 roads
- emergency_coordination → db.persist_recommendation: 3 plans, 3 pending actions, 2 superseded, 1 alerts

</details>

<details><summary>API calls</summary>

- POST /api/simulation/scenario/TC-05 -> 200
- GET /api/assessments/RUN-002 -> 200
- GET /api/actions?status=approved -> 200
- GET /api/actions?status=superseded -> 200
- GET /api/alerts -> 200
- GET /api/situation -> 200
- GET /api/access/INC-002 -> 200
- GET /api/actions/ACT-004/events -> 200

</details>

**Final state**

4 active incident(s); critical: INC-003. Recommended PLAN-005 (Plan B: Coverage) with 3 action(s) pending coordinator approval. Shortages: rescue_boat deficit 12; rescue_team deficit 2. 6 unresolved item(s).

---

## TC-06: Shelter approaches capacity

**Result:** ✅ PASS

**Input**

Shelter A occupancy updates 160 → 168 (84%) → 172 (86%), then → 200 (full).

**Initial system state**

Base seed: INC-001..INC-004 (Zone A/B/C floods; INC-004 citizen duplicate of INC-002), 23 resources (BOAT-001/TEAM-001 deployed on INC-001, AMB-006 returning, VEH-008 unavailable), RD-C known closed, RD-C2 AI-inferred, simulated heavy rain; SHL-A 160/200

**Expected output**

- No alert at 84%
- Exactly one shelter_capacity warning when 85% is crossed
- At 200/200 the shelter becomes full, a critical alert is raised and available capacity is 0

**Actual output**

- Setup applied: shelter SHL-A -> 168; shelter SHL-A -> 172
- Alerts after 168 and 172: ['Shelter A (SHL-A) at 86% (172/200); alert threshold 85%']
- After 200/200: status=full, alerts=['critical: Shelter A (SHL-A) is FULL: direct evacuees elsewhere', 'warning: Shelter A (SHL-A) at 86% (172/200); alert threshold 85%']

**Checks**

| Check | Result | Detail |
|---|---|---|
| exactly one alert after crossing 85% | ✅ | ['Shelter A (SHL-A) at 86% (172/200); alert threshold 85%'] |
| status full and available capacity 0 | ✅ | full |
| critical 'full' alert | ✅ | 2 |

**Agents involved**

incident_intake, resource_management, weather_intelligence, geospatial_accessibility, situation_assessment, resource_allocation, reviewer_critic, emergency_coordination

<details><summary>Tool calls (from the agent trace)</summary>

- incident_intake → raw_reports.fetch_pending: 0 pending report(s)
- resource_management → resource_registry.list: 23 resources
- resource_management → shelter_registry.list: 3 shelters
- weather_intelligence → weather.get: simulation: Heavy rain, 18 mm/h, wind 32 km/h, severe=True
- weather_intelligence → weather.get: simulation: Heavy rain, 24 mm/h, wind 32 km/h, severe=True
- geospatial_accessibility → road_registry.list: 4 road segments
- geospatial_accessibility → osrm.table: 76 routed, 0 estimated
- geospatial_accessibility → postgis.nearest_shelters: SHL-A 1341 m, SHL-C 2469 m, SHL-B 3878 m
- geospatial_accessibility → postgis.nearest_shelters: SHL-B 1630 m, SHL-A 3273 m, SHL-C 3364 m
- geospatial_accessibility → postgis.nearest_shelters: SHL-C 3888 m, SHL-A 7417 m, SHL-B 8468 m
- geospatial_accessibility → postgis.nearest_shelters: SHL-B 1544 m, SHL-A 3247 m, SHL-C 3428 m
- situation_assessment → priority.assess: 61 high
- situation_assessment → priority.assess: 58 high
- situation_assessment → priority.assess: 89 critical
- situation_assessment → priority.assess: 50 moderate
- resource_allocation → allocation.generate_plans: A score 17.6; B score 37.9 (recommended); C score 17.6
- resource_allocation → llm.explain_plan: fallback (AuthenticationError): grounded [fallback/failed]
- reviewer_critic → resource_registry.list: 23 resources re-read
- reviewer_critic → road_registry.list: 4 roads
- emergency_coordination → db.persist_recommendation: 3 plans, 3 pending actions, 0 superseded, 4 alerts

</details>

<details><summary>API calls</summary>

- POST /api/simulation/scenario/TC-06 -> 200
- GET /api/assessments/RUN-001 -> 200
- GET /api/alerts -> 200
- GET /api/assessments?limit=1 -> 200
- PATCH /api/shelters/SHL-A -> 200
- GET /api/assessments?limit=1 -> 200
- GET /api/assessments?limit=1 -> 200
- GET /api/assessments?limit=1 -> 200
- GET /api/alerts -> 200

</details>

**Final state**

SHL-A 200/200 (full); 2 shelter alerts

---

## TC-07: Weather forecast worsens

**Result:** ✅ PASS

**Input**

New weather for Zone C: 38 mm/h, wind 55 km/h, severe warning, very heavy rain for 6 h.

**Initial system state**

Base seed: INC-001..INC-004 (Zone A/B/C floods; INC-004 citizen duplicate of INC-002), 23 resources (BOAT-001/TEAM-001 deployed on INC-001, AMB-006 returning, VEH-008 unavailable), RD-C known closed, RD-C2 AI-inferred, simulated heavy rain; one run done (INC-003 = 89)

**Expected output**

- Weather agent detects worsening → weather_worsening alert for Zone C
- INC-003 reassessed: weather factor 13 → 15 (wind ≥ 50 km/h), score 89 → 91
- Incidents in other zones unchanged by this update

**Actual output**

- Setup applied: base seed
- Injection reported worsened=True
- Alert: ['Weather worsening in Zone C: Very heavy rain, 38 mm/h, wind 55 km/h. Very heavy rain and strong winds for the next 6 hours']
- INC-003: 89 → 91 (weather factor 13 → 15)

**Checks**

| Check | Result | Detail |
|---|---|---|
| worsening detected | ✅ | 1 |
| INC-003 weather factor 13 → 15 | ✅ |  |
| INC-003 score 89 → 91 (still critical) | ✅ |  |
| Zone A/B incidents unchanged | ✅ |  |

**Agents involved**

incident_intake, resource_management, weather_intelligence, geospatial_accessibility, situation_assessment, resource_allocation, reviewer_critic, emergency_coordination

<details><summary>Tool calls (from the agent trace)</summary>

- incident_intake → raw_reports.fetch_pending: 0 pending report(s)
- resource_management → resource_registry.list: 23 resources
- resource_management → shelter_registry.list: 3 shelters
- weather_intelligence → weather.get: simulation: Heavy rain, 18 mm/h, wind 32 km/h, severe=True
- weather_intelligence → weather.get: simulation: Heavy rain, 24 mm/h, wind 32 km/h, severe=True
- geospatial_accessibility → road_registry.list: 4 road segments
- geospatial_accessibility → osrm.table: 76 routed, 0 estimated
- geospatial_accessibility → postgis.nearest_shelters: SHL-A 1341 m, SHL-C 2469 m, SHL-B 3878 m
- geospatial_accessibility → postgis.nearest_shelters: SHL-B 1630 m, SHL-A 3273 m, SHL-C 3364 m
- geospatial_accessibility → postgis.nearest_shelters: SHL-C 3888 m, SHL-A 7417 m, SHL-B 8468 m
- geospatial_accessibility → postgis.nearest_shelters: SHL-B 1544 m, SHL-A 3247 m, SHL-C 3428 m
- situation_assessment → priority.assess: 61 high
- situation_assessment → priority.assess: 58 high
- situation_assessment → priority.assess: 89 critical
- situation_assessment → priority.assess: 50 moderate
- resource_allocation → allocation.generate_plans: A score 17.6; B score 37.9 (recommended); C score 17.6
- resource_allocation → llm.explain_plan: fallback (AuthenticationError): grounded [fallback/failed]
- reviewer_critic → resource_registry.list: 23 resources re-read
- reviewer_critic → road_registry.list: 4 roads
- emergency_coordination → db.persist_recommendation: 3 plans, 3 pending actions, 0 superseded, 4 alerts
- weather_intelligence → weather.get: simulation: Very heavy rain, 38 mm/h, wind 55 km/h, severe=True
- situation_assessment → priority.assess: 91 critical
- resource_allocation → allocation.generate_plans: A score 17.8; B score 37.7 (recommended); C score 17.8
- emergency_coordination → db.persist_recommendation: 3 plans, 3 pending actions, 2 superseded, 1 alerts

</details>

<details><summary>API calls</summary>

- POST /api/simulation/scenario/TC-03 -> 200
- GET /api/assessments/RUN-001 -> 200
- GET /api/incidents -> 200
- GET /api/assessments?limit=1 -> 200
- POST /api/simulation/weather -> 200
- GET /api/assessments?limit=1 -> 200
- GET /api/assessments?limit=1 -> 200
- GET /api/assessments?limit=1 -> 200
- GET /api/assessments/RUN-002 -> 200
- GET /api/incidents -> 200
- GET /api/alerts -> 200

</details>

**Final state**

Run RUN-002 (trigger weather_change)

---

## TC-08: Same resource considered for two incidents

**Result:** ✅ PASS

**Input**

Only one rescue boat (BOAT-005) remains available while Zone B and Zone C both need boats. A coordinator then edits a second action to use BOAT-005 and approves both.

**Initial system state**

Base seed: INC-001..INC-004 (Zone A/B/C floods; INC-004 citizen duplicate of INC-002), 23 resources (BOAT-001/TEAM-001 deployed on INC-001, AMB-006 returning, VEH-008 unavailable), RD-C known closed, RD-C2 AI-inferred, simulated heavy rain; BOAT-002, BOAT-003, BOAT-004 unavailable

**Expected output**

- Engine: BOAT-005 appears at most once in every plan
- First approval reserves BOAT-005; the second approval is rejected with HTTP 409
- Database holds exactly one active assignment for BOAT-005; the stale action is superseded and re-planned

**Actual output**

- Setup applied: resource BOAT-002 -> unavailable; resource BOAT-003 -> unavailable; resource BOAT-004 -> unavailable
- BOAT-005 occurrences per plan: {'A': 1, 'B': 1, 'C': 1}
- First approval of ACT-002: approved
- Second approval of ACT-004: 409 'Cannot approve ACT-004: BOAT-005 is reserved (assigned to INC-003). The action was superseded and a new recommendation is being prepared.'
- BOAT-005 status reserved assigned to INC-003; ACT-004 now superseded

**Checks**

| Check | Result | Detail |
|---|---|---|
| engine: BOAT-005 at most once per plan | ✅ | {'A': 1, 'B': 1, 'C': 1} |
| two pending actions to compete | ✅ | ['ACT-004', 'ACT-003', 'ACT-002'] |
| first approval succeeded | ✅ | approved |
| second approval returned 409 naming BOAT-005 | ✅ | Cannot approve ACT-004: BOAT-005 is reserved (assigned to INC-003). The action was superseded and a new recommendation is being prepared. |
| exactly one holder of BOAT-005 | ✅ | {'id': 'BOAT-005', 'type': 'rescue_boat', 'name': 'Rescue Boat 5', 'base_name': 'Response Center 1', 'location': {'lat': 13.0432, 'lng': 80.2532}, 'status': 'reserved', 'capacity': |
| stale action superseded (re-plan triggered) | ✅ | superseded |

**Agents involved**

incident_intake, resource_management, weather_intelligence, geospatial_accessibility, situation_assessment, resource_allocation, reviewer_critic, emergency_coordination

<details><summary>Tool calls (from the agent trace)</summary>

- incident_intake → raw_reports.fetch_pending: 0 pending report(s)
- resource_management → resource_registry.list: 23 resources
- resource_management → shelter_registry.list: 3 shelters
- weather_intelligence → weather.get: simulation: Heavy rain, 18 mm/h, wind 32 km/h, severe=True
- weather_intelligence → weather.get: simulation: Heavy rain, 24 mm/h, wind 32 km/h, severe=True
- geospatial_accessibility → road_registry.list: 4 road segments
- geospatial_accessibility → osrm.table: 64 routed, 0 estimated
- geospatial_accessibility → postgis.nearest_shelters: SHL-A 1341 m, SHL-C 2469 m, SHL-B 3878 m
- geospatial_accessibility → postgis.nearest_shelters: SHL-B 1630 m, SHL-A 3273 m, SHL-C 3364 m
- geospatial_accessibility → postgis.nearest_shelters: SHL-C 3888 m, SHL-A 7417 m, SHL-B 8468 m
- geospatial_accessibility → postgis.nearest_shelters: SHL-B 1544 m, SHL-A 3247 m, SHL-C 3428 m
- situation_assessment → priority.assess: 61 high
- situation_assessment → priority.assess: 58 high
- situation_assessment → priority.assess: 89 critical
- situation_assessment → priority.assess: 50 moderate
- resource_allocation → allocation.generate_plans: A score 10.1; B score 20.2 (recommended); C score 10.1
- resource_allocation → llm.explain_plan: fallback (AuthenticationError): grounded [fallback/failed]
- reviewer_critic → resource_registry.list: 23 resources re-read
- reviewer_critic → road_registry.list: 4 roads
- emergency_coordination → db.persist_recommendation: 3 plans, 3 pending actions, 0 superseded, 4 alerts

</details>

<details><summary>API calls</summary>

- POST /api/simulation/scenario/TC-08 -> 200
- GET /api/assessments/RUN-001 -> 200
- GET /api/plans/latest -> 200
- GET /api/actions?status=pending -> 200
- POST /api/actions/ACT-004/modify -> 200
- POST /api/actions/ACT-002/approve -> 200
- GET /api/assessments?limit=1 -> 200
- POST /api/actions/ACT-004/approve -> 409
- GET /api/assessments?limit=1 -> 200
- GET /api/assessments?limit=1 -> 200
- GET /api/assessments?limit=1 -> 200
- GET /api/resources -> 200
- GET /api/actions/ACT-004 -> 200

</details>

**Final state**

BOAT-005 reserved for INC-003 via ACT-002
