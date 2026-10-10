"""TC-01...TC-08, written once against the HTTP API.

Each test case drives the system only through public endpoints and returns a TCResult with:
input, initial state, expected output, actual output, agents involved, tool/API calls (from the agent
trace), final state and pass/fail per check. The same functions run in pytest (FastAPI TestClient,
LLM mocked) and in `python -m app.tools.run_scenarios` against a live server.

`client` is anything with .get/.post/.patch(path, json=...) returning a response with .status_code and
.json(): starlette's TestClient and httpx.Client both qualify.
"""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

BOAT_ASSUMPTION = "Water and route conditions permit boat deployment: verification required"


class Client(Protocol):
    def get(self, url: str, **kw: Any) -> Any: ...
    def post(self, url: str, **kw: Any) -> Any: ...
    def patch(self, url: str, **kw: Any) -> Any: ...


@dataclass
class TCResult:
    tc: str
    title: str
    input: str
    initial_state: str
    expected: list[str]
    actual: list[str] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)
    api_calls: list[str] = field(default_factory=list)
    final_state: str = ""
    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(ok for _, ok, _ in self.checks)

    def check(self, name: str, ok: bool, detail: Any = "") -> bool:
        self.checks.append((name, bool(ok), str(detail)))
        return bool(ok)


class Api:
    """Thin wrapper that records every API call for the report and fails loudly on unexpected statuses."""

    def __init__(self, client: Client, result: TCResult):
        self.c, self.r = client, result

    def call(self, method: str, path: str, body: Any = None, expect: tuple[int, ...] = (200, 201, 202)) -> Any:
        resp = getattr(self.c, method)(path, **({"json": body} if body is not None else {}))
        self.r.api_calls.append(f"{method.upper()} {path} -> {resp.status_code}")
        if resp.status_code not in expect:
            raise AssertionError(f"{method.upper()} {path} returned {resp.status_code}: {resp.text[:300]}")
        return resp.json() if resp.content else None

    def get(self, path: str) -> Any:
        return self.call("get", path)

    def post(self, path: str, body: Any = None, expect: tuple[int, ...] = (200, 201, 202)) -> Any:
        return self.call("post", path, body if body is not None else {}, expect)

    def patch(self, path: str, body: Any) -> Any:
        return self.call("patch", path, body)

    # ------------------------------------------------------------ helpers

    def latest_run_id(self) -> str | None:
        runs = self.get("/api/assessments?limit=1")
        return runs[0]["id"] if runs else None

    def wait_for_run_after(self, previous: str | None, timeout_s: float = 90) -> dict:
        """Background runs finish after the HTTP response on a live server; poll until a new run completes."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            runs = self.get("/api/assessments?limit=1")
            if runs and runs[0]["id"] != previous and runs[0]["status"] != "running":
                return runs[0]
            time.sleep(0.5)
        raise AssertionError(f"no new agent run completed within {timeout_s} s")

    def record_trace(self, run_id: str) -> dict:
        run = self.get(f"/api/assessments/{run_id}")
        for step in run["steps"]:
            if step["agent"] not in self.r.agents:
                self.r.agents.append(step["agent"])
            for t in step["tool_calls"]:
                line = f"{step['agent']} → {t['name']}: {t['result_summary']}" + ("" if t["ok"] else " [fallback/failed]")
                if line not in self.r.tool_calls:
                    self.r.tool_calls.append(line)
        return run

    def incidents(self) -> dict[str, dict]:
        return {i["id"]: i for i in self.get("/api/incidents")}

    def pending(self) -> list[dict]:
        return self.get("/api/actions?status=pending")


def _setup(api: Api, tc: str) -> dict:
    body = api.post(f"/api/simulation/scenario/{tc}")
    api.r.actual.append(f"Setup applied: {'; '.join(body['applied']) or 'base seed'}")
    api.record_trace(body["run"]["run_id"])
    return body


SEED_STATE = ("Base seed: INC-001..INC-004 (Zone A/B/C floods; INC-004 citizen duplicate of INC-002), 23 resources "
              "(BOAT-001/TEAM-001 deployed on INC-001, AMB-006 returning, VEH-008 unavailable), RD-C known closed, "
              "RD-C2 AI-inferred, simulated heavy rain")


# ---------------------------------------------------------------- TC-01

def tc01(client: Client) -> TCResult:
    r = TCResult("TC-01", "New flood incident reported",
                 input="Emergency call (free text): flood water in ground-floor homes on River Street, Zone D; about 25 people "
                       "including elderly residents need evacuation; one woman with a leg injury; cars cannot get through.",
                 initial_state=SEED_STATE,
                 expected=["Report normalized into a flood incident in Zone D with people_affected = 25 (reported)",
                           "Location resolved via the zone gazetteer and marked inferred (approximate)",
                           "Priority assessed with all six factors and their provenance",
                           "Recommendation created only as a pending action"])
    api = Api(client, r)
    body = _setup(api, "TC-01")
    new = [i for i in api.incidents().values() if i["zone"] == "Zone D"]
    inc = new[0] if new else {}
    p = inc.get("priority") or {}
    r.actual += [f"Created {inc.get('id')}: type={inc.get('type')}, zone={inc.get('zone')}, people={inc.get('people_affected')}, "
                 f"assistance={inc.get('assistance_requested')}, confidence={inc.get('confidence')}",
                 f"Priority {p.get('score')} ({p.get('level')}): " + "; ".join(f"{f['name']} {f['points']}/{f['max_points']} [{f['provenance']}]" for f in p.get("factors", []))]
    r.check("exactly one new incident in Zone D", len(new) == 1, [i["id"] for i in new])
    r.check("type flood, people 25", (inc.get("type"), inc.get("people_affected")) == ("flood", 25), (inc.get("type"), inc.get("people_affected")))
    r.check("evacuation and medical help extracted", {"evacuation", "medical_check"} <= set(inc.get("assistance_requested", [])), inc.get("assistance_requested"))
    r.check("priority has 6 factors with provenance", len(p.get("factors", [])) == 6 and all(f["provenance"] for f in p["factors"]), len(p.get("factors", [])))
    r.check("Intake agent recorded an extraction call", any("extract_incident" in t for t in r.tool_calls), "")
    pend = [a for a in api.pending() if a["incident_id"] == inc.get("id")]
    r.check("any recommendation for it is pending (not dispatched)", all(a["status"] == "pending" for a in pend), len(pend))
    r.final_state = f"{len(api.incidents())} active incidents; run {body['run']['run_id']}: {body['run']['summary']}"
    return r


# ---------------------------------------------------------------- TC-02

def tc02(client: Client) -> TCResult:
    r = TCResult("TC-02", "Same incident from two sources",
                 input="(1) Emergency call about River Street, Zone D (25 people). (2) Ten minutes later a citizen message: "
                       "'water flooding river street zone D!! lots of people stuck, old people need help, pls send boat'.",
                 initial_state=SEED_STATE,
                 expected=["Second report becomes its own incident (nothing deleted or merged)",
                           "A potential_duplicate link is created with status pending and a full score breakdown",
                           "The duplicate does not receive a separate allocation"])
    api = Api(client, r)
    _setup(api, "TC-02")
    incs = api.incidents()
    call = next((i for i in incs.values() if i["zone"] == "Zone D" and i["source"] == "emergency_call"), None)
    citizen = next((i for i in incs.values() if i["zone"] == "Zone D" and i["source"] == "citizen_report"), None)
    r.check("both reports exist as incidents", call is not None and citizen is not None, [i["id"] for i in incs.values() if i["zone"] == "Zone D"])
    if not (call and citizen):
        return r
    links = api.get(f"/api/incidents/{citizen['id']}/duplicates")
    link = next((lk for lk in links if call["id"] in (lk["incident_id"], lk["candidate_id"])), None)
    r.actual.append(f"Link: {link and {k: link[k] for k in ('id', 'score', 'classification', 'status')}}; breakdown {link and link['breakdown']}")
    r.actual.append(f"{citizen['id']}: people={citizen['people_affected']} (vague count kept unknown), confidence={citizen['confidence']}")
    r.check("potential_duplicate link, pending review", bool(link) and (link["classification"], link["status"]) == ("potential_duplicate", "pending"), link)
    r.check("score is the documented weighted sum", bool(link) and abs(link["score"] - (0.35 * link["breakdown"]["distance_score"] + 0.25 * link["breakdown"]["time_score"]
                                                                                      + 0.15 * link["breakdown"]["type_match"] + 0.25 * link["breakdown"]["text_similarity"])) < 0.01, link and link["score"])
    r.check("citizen people count stays unknown", citizen["people_affected"] is None, citizen["people_affected"])
    r.check("duplicate exposed as duplicate_of", citizen["duplicate_of"] == call["id"], citizen["duplicate_of"])
    r.check("no separate action for the duplicate", not any(a["incident_id"] == citizen["id"] for a in api.pending()), "")
    r.final_state = f"{citizen['id']} linked to {call['id']} for human review ({link and link['id']})"
    return r


# ---------------------------------------------------------------- TC-03

def tc03(client: Client) -> TCResult:
    r = TCResult("TC-03", "Critical incident with available resources",
                 input="Assessment run on the base seed (INC-003: village in Zone C cut off, 75 people, main road closed).",
                 initial_state=SEED_STATE,
                 expected=["INC-003 scored 89 → Critical with factor breakdown",
                           "Allocation recommends available boats/teams (no road vehicles through the known closure)",
                           "Action created as pending with rationale, assumptions and inputs used; no resource reserved"])
    api = Api(client, r)
    body = _setup(api, "TC-03")
    inc = api.incidents()["INC-003"]
    act = next((a for a in api.pending() if a["incident_id"] == "INC-003"), None)
    resources = {x["id"]: x for x in api.get("/api/resources")}
    plans = api.get("/api/plans/latest")
    r.actual += [f"INC-003 priority {inc['priority']['score']} {inc['priority']['level']}",
                 f"Pending {act and act['id']}: {act and act['resource_ids']}; assumptions: {act and act['assumptions']}",
                 f"Recommended plan: {next((p['id'] + ' ' + p['title'] for p in plans if p['is_recommended']), None)}"]
    r.check("INC-003 = 89 Critical", (inc["priority"]["score"], inc["priority"]["level"]) == (89, "critical"), inc["priority"]["score"])
    r.check("pending action for INC-003", act is not None, "")
    if act:
        types = {resources[i]["type"] for i in act["resource_ids"]}
        r.check("only water-capable resources (boats/teams) to the closed zone", types <= {"rescue_boat", "rescue_team"}, types)
        r.check("boat verification assumption shown", BOAT_ASSUMPTION in act["assumptions"], "")
        r.check("rationale and inputs with provenance present", bool(act["rationale"]) and all(i["provenance"] for i in act["inputs_used"]), len(act["inputs_used"]))
        r.check("resources NOT reserved before approval", all(resources[i]["status"] == "available" for i in act["resource_ids"]),
                {i: resources[i]["status"] for i in act["resource_ids"]})
    r.check("reviewer passed the recommended plan", not any(f["severity"] == "blocking" for p in plans if p["is_recommended"] for f in p["critic_findings"]), "")
    r.final_state = body["run"]["summary"]
    return r


# ---------------------------------------------------------------- TC-04

def tc04(client: Client) -> TCResult:
    r = TCResult("TC-04", "Required resources exceed availability",
                 input="BOAT-004 (engine failure, back in ~3 h) and BOAT-005 (hull damage) become unavailable; "
                       "INC-003 people count revised from 75 to 120 (reported).",
                 initial_state=SEED_STATE,
                 expected=["Boat requirement computed by the engine: INC-003 ceil(120/8)=15 + INC-001 (5−1 assigned)=4 + INC-002 2 = 21",
                           "Available boats = 2 (BOAT-002, BOAT-003) → exact deficit 19 with an escalation note",
                           "No invented resources in any plan"])
    api = Api(client, r)
    _setup(api, "TC-04")
    plans = api.get("/api/plans/latest")
    rec = next(p for p in plans if p["is_recommended"])
    boats = next((s for s in rec["shortages"] if s["resource_type"] == "rescue_boat"), None)
    registry = {x["id"] for x in api.get("/api/resources")}
    used = {res["resource_id"] for p in plans for a in p["allocations"] for res in a["resources"]}
    r.actual += [f"Boat shortage: {boats}", f"Resources used across plans: {sorted(used)}"]
    r.check("required = 21 boats (engine rules)", boats is not None and boats["required"] == 21 and math.ceil(120 / 8) == 15, boats and boats["required"])
    r.check("available = 2, deficit = 19", boats is not None and (boats["available"], boats["deficit"]) == (2, 19), boats and (boats["available"], boats["deficit"]))
    r.check("escalation note, no invented resource", boats is not None and "Escalate" in boats["note"], boats and boats["note"])
    r.check("every allocated resource exists in the registry", used <= registry, sorted(used - registry))
    r.check("BOAT-004/BOAT-005 not allocated", not {"BOAT-004", "BOAT-005"} & used, "")
    r.final_state = f"Recommended {rec['id']}; shortages: " + "; ".join(f"{s['resource_type']} −{s['deficit']}" for s in rec["shortages"])
    return r


# ---------------------------------------------------------------- TC-05

def tc05(client: Client) -> TCResult:
    r = TCResult("TC-05", "Assigned road becomes blocked",
                 input="After the coordinator approves the Zone B recommendation, Main Road to Zone B (RD-B) is reported "
                       "CLOSED by the traffic police (known).",
                 initial_state=SEED_STATE + "; one run done and INC-002's action approved (resources reserved)",
                 expected=["Condition change recorded (RD-B open → closed, known)",
                           "route_blocked alert (critical)",
                           "Pending recommendations superseded; the approved action is flagged for coordinator review, not changed",
                           "New run: Zone B access becomes inaccessible by road and a new recommendation is produced"])
    api = Api(client, r)
    body = _setup(api, "TC-05")
    approved = [a for a in api.get("/api/actions?status=approved") if a["incident_id"] == "INC-002"]
    superseded = api.get("/api/actions?status=superseded")
    alerts = [a for a in api.get("/api/alerts") if a["kind"] == "route_blocked" and a["related_id"] == "RD-B"]
    situation = api.get("/api/situation")
    change = next((c for c in situation["changes"] if c["entity"] == "Main Road to Zone B" and c["field"] == "status"), None)
    access = api.get("/api/access/INC-002")
    events = api.get(f"/api/actions/{approved[0]['id']}/events") if approved else []
    r.actual += [f"Change: {change}", f"Alerts: {[a['message'] for a in alerts]}",
                 f"Approved action {approved and approved[0]['id']} events: {[e['event'] + ': ' + (e['comment'] or '') for e in events]}",
                 f"Superseded actions: {[a['id'] for a in superseded]}", f"INC-002 access now: {access['access_status']}"]
    r.check("condition change recorded with provenance known", change is not None and (change["old_value"], change["new_value"], change["provenance"]) == ("open", "closed", "known"), change)
    r.check("route_blocked critical alert", any(a["severity"] == "critical" for a in alerts), len(alerts))
    r.check("approved action kept and flagged for review", bool(approved) and any("review required" in (e["comment"] or "") for e in events), "")
    r.check("earlier pending recommendations superseded", len(superseded) >= 1, len(superseded))
    r.check("Zone B now inaccessible by road", access["access_status"] == "inaccessible_by_road", access["access_status"])
    r.check("new recommendation produced after the change", bool(body["run"]["recommended_plan_id"]), body["run"]["recommended_plan_id"])
    r.final_state = body["run"]["summary"]
    return r


# ---------------------------------------------------------------- TC-06

def tc06(client: Client) -> TCResult:
    r = TCResult("TC-06", "Shelter approaches capacity",
                 input="Shelter A occupancy updates 160 → 168 (84%) → 172 (86%), then → 200 (full).",
                 initial_state=SEED_STATE + "; SHL-A 160/200",
                 expected=["No alert at 84%", "Exactly one shelter_capacity warning when 85% is crossed",
                           "At 200/200 the shelter becomes full, a critical alert is raised and available capacity is 0"])
    api = Api(client, r)
    _setup(api, "TC-06")
    first = [a for a in api.get("/api/alerts") if a["kind"] == "shelter_capacity"]
    r.actual.append(f"Alerts after 168 and 172: {[a['message'] for a in first]}")
    r.check("exactly one alert after crossing 85%", len(first) == 1 and first[0]["severity"] == "warning" and "86%" in first[0]["message"], [a["message"] for a in first])
    before = api.latest_run_id()
    shelter = api.patch("/api/shelters/SHL-A", {"current_occupancy": 200})
    if shelter["status"] == "full":
        api.wait_for_run_after(before)  # full shelter -> evacuation incidents reassessed
    alerts = [a for a in api.get("/api/alerts") if a["kind"] == "shelter_capacity"]
    r.actual.append(f"After 200/200: status={shelter['status']}, alerts={[a['severity'] + ': ' + a['message'] for a in alerts]}")
    r.check("status full and available capacity 0", shelter["status"] == "full" and shelter["capacity"] - shelter["current_occupancy"] == 0, shelter["status"])
    r.check("critical 'full' alert", any(a["severity"] == "critical" for a in alerts), len(alerts))
    r.final_state = f"SHL-A {shelter['current_occupancy']}/{shelter['capacity']} ({shelter['status']}); {len(alerts)} shelter alerts"
    return r


# ---------------------------------------------------------------- TC-07

def tc07(client: Client) -> TCResult:
    r = TCResult("TC-07", "Weather forecast worsens",
                 input="New weather for Zone C: 38 mm/h, wind 55 km/h, severe warning, very heavy rain for 6 h.",
                 initial_state=SEED_STATE + "; one run done (INC-003 = 89)",
                 expected=["Weather agent detects worsening → weather_worsening alert for Zone C",
                           "INC-003 reassessed: weather factor 13 → 15 (wind ≥ 50 km/h), score 89 → 91",
                           "Incidents in other zones unchanged by this update"])
    api = Api(client, r)
    _setup(api, "TC-03")
    before = api.incidents()
    run_before = api.latest_run_id()
    inject = api.post("/api/simulation/weather", {
        "zone": "Zone C", "condition": "Very heavy rain", "rainfall_mm_per_hr": 38, "wind_kph": 55, "temperature_c": 25,
        "forecast_summary": "Very heavy rain and strong winds for the next 6 hours", "severe_warning": True,
        "forecast_hourly": [{"hour": h, "precipitation_mm": v, "wind_kph": w} for h, v, w in
                            [(1, 36, 55), (2, 40, 58), (3, 38, 56), (4, 33, 52), (5, 28, 48), (6, 25, 45)]]})
    run = api.wait_for_run_after(run_before)
    api.record_trace(run["id"])
    after = api.incidents()
    factor = lambda inc: next(f["points"] for f in inc["priority"]["factors"] if f["name"] == "Weather / escalation")  # noqa: E731
    alerts = [a for a in api.get("/api/alerts") if a["kind"] == "weather_worsening" and "Zone C" in a["message"]
              and a["message"].startswith("Weather worsening")]
    r.actual += [f"Injection reported worsened={inject['worsened']}", f"Alert: {[a['message'] for a in alerts]}",
                 f"INC-003: {before['INC-003']['priority']['score']} → {after['INC-003']['priority']['score']} "
                 f"(weather factor {factor(before['INC-003'])} → {factor(after['INC-003'])})"]
    r.check("worsening detected", inject["worsened"] and len(alerts) == 1, len(alerts))
    r.check("INC-003 weather factor 13 → 15", (factor(before["INC-003"]), factor(after["INC-003"])) == (13, 15), "")
    r.check("INC-003 score 89 → 91 (still critical)", (before["INC-003"]["priority"]["score"], after["INC-003"]["priority"]["score"],
                                                       after["INC-003"]["priority"]["level"]) == (89, 91, "critical"), "")
    r.check("Zone A/B incidents unchanged", all(before[i]["priority"]["score"] == after[i]["priority"]["score"] for i in ("INC-001", "INC-002")), "")
    r.final_state = f"Run {run['id']} (trigger {run['trigger']})"
    return r


# ---------------------------------------------------------------- TC-08

def tc08(client: Client) -> TCResult:
    r = TCResult("TC-08", "Same resource considered for two incidents",
                 input="Only one rescue boat (BOAT-005) remains available while Zone B and Zone C both need boats. "
                       "A coordinator then edits a second action to use BOAT-005 and approves both.",
                 initial_state=SEED_STATE + "; BOAT-002, BOAT-003, BOAT-004 unavailable",
                 expected=["Engine: BOAT-005 appears at most once in every plan",
                           "First approval reserves BOAT-005; the second approval is rejected with HTTP 409",
                           "Database holds exactly one active assignment for BOAT-005; the stale action is superseded and re-planned"])
    api = Api(client, r)
    _setup(api, "TC-08")
    plans = api.get("/api/plans/latest")
    counts = {p["variant"]: sum(res["resource_id"] == "BOAT-005" for a in p["allocations"] for res in a["resources"]) for p in plans}
    r.actual.append(f"BOAT-005 occurrences per plan: {counts}")
    r.check("engine: BOAT-005 at most once per plan", all(n <= 1 for n in counts.values()), counts)
    pending = api.pending()
    holder = next((a for a in pending if "BOAT-005" in a["resource_ids"]), None)
    other = next((a for a in pending if holder and a["id"] != holder["id"]), None)
    r.check("two pending actions to compete", holder is not None and other is not None, [a["id"] for a in pending])
    if not (holder and other):
        return r
    api.post(f"/api/actions/{other['id']}/modify", {"resource_ids": ["BOAT-005"], "comment": "TC-08: force contention"})
    first = api.post(f"/api/actions/{holder['id']}/approve", {"comment": "TC-08 first"})
    run_before = api.latest_run_id()
    second = api.call("post", f"/api/actions/{other['id']}/approve", {"comment": "TC-08 second"}, expect=(409,))
    api.wait_for_run_after(run_before)
    assignments = [x for x in api.get("/api/resources") if x["id"] == "BOAT-005"][0]
    stale = api.get(f"/api/actions/{other['id']}")
    r.actual += [f"First approval of {holder['id']}: {first['status']}", f"Second approval of {other['id']}: 409 '{second['detail']}'",
                 f"BOAT-005 status {assignments['status']} assigned to {assignments['assigned_incident_id']}; {other['id']} now {stale['status']}"]
    r.check("first approval succeeded", first["status"] == "approved", first["status"])
    r.check("second approval returned 409 naming BOAT-005", "BOAT-005" in second["detail"], second["detail"])
    r.check("exactly one holder of BOAT-005", assignments["status"] == "reserved" and assignments["assigned_incident_id"] == holder["incident_id"], assignments)
    r.check("stale action superseded (re-plan triggered)", stale["status"] == "superseded", stale["status"])
    r.final_state = f"BOAT-005 reserved for {holder['incident_id']} via {holder['id']}"
    return r


TEST_CASES: list[Callable[[Client], TCResult]] = [tc01, tc02, tc03, tc04, tc05, tc06, tc07, tc08]


def run_safely(fn: Callable[[Client], TCResult], client: Client) -> TCResult:
    """Run one TC; an unexpected exception becomes a failed check instead of aborting the whole report."""
    try:
        return fn(client)
    except Exception as exc:  # noqa: BLE001
        r = TCResult(fn.__name__.upper().replace("TC", "TC-"), fn.__name__, "", "", [])
        r.check("test case ran to completion", False, f"{type(exc).__name__}: {exc}")
        return r
