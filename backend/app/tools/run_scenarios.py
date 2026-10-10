"""Run TC-01...TC-08 against a LIVE backend and write docs/TEST_REPORT.md.

    uvicorn app.main:app --port 8000          (WEATHER_MODE=simulation is required for TC-07)
    python -m app.tools.run_scenarios [--base-url http://localhost:8000] [--out ../docs/TEST_REPORT.md]

Every test case resets the database to the seed scenario first, so run this against a demo database,
never against data you want to keep.
"""

import argparse
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.core.config import BACKEND_DIR
from app.tools.test_cases import TEST_CASES, TCResult, run_safely

DEFAULT_OUT = BACKEND_DIR.parent / "docs" / "TEST_REPORT.md"


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "- (none)"


def render(results: list[TCResult], health: dict, base_url: str) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    passed = sum(r.passed for r in results)
    model = f" ({health['llm_model']})" if health.get("llm_model") else ""
    lines = [
        "# Test Report: TC-01 … TC-08",
        "",
        f"Generated {now} by `python -m app.tools.run_scenarios` against `{base_url}`.",
        f"Backend: LLM **{health.get('llm')}**{model}, "
        f"weather **{health.get('weather_mode')}**, routing **{health.get('routing_mode')}**, PostGIS {str(health.get('postgis', '')).split(' ')[0]}.",
        "",
        "Every test case starts from a reset seed database and drives the system only through the public HTTP API. "
        "Agents involved and tool calls are taken from the persisted agent trace (`GET /api/assessments/{run_id}`).",
        "",
        f"**Result: {passed}/{len(results)} passed.**",
        "",
        "| TC | Scenario | Result | Checks |",
        "|---|---|---|---|",
    ]
    for r in results:
        ok = sum(c[1] for c in r.checks)
        lines.append(f"| {r.tc} | {r.title} | {'✅ Pass' if r.passed else '❌ Fail'} | {ok}/{len(r.checks)} |")
    for r in results:
        lines += [
            "", "---", "", f"## {r.tc}: {r.title}", "",
            f"**Result:** {'✅ PASS' if r.passed else '❌ FAIL'}", "",
            "**Input**", "", r.input or "-", "",
            "**Initial system state**", "", r.initial_state or "-", "",
            "**Expected output**", "", _bullets(r.expected), "",
            "**Actual output**", "", _bullets(r.actual), "",
            "**Checks**", "",
            "| Check | Result | Detail |", "|---|---|---|",
            *[f"| {name} | {'✅' if ok else '❌'} | {detail.replace('|', '/')[:180]} |" for name, ok, detail in r.checks],
            "", "**Agents involved**", "", ", ".join(r.agents) or "-", "",
            "<details><summary>Tool calls (from the agent trace)</summary>", "", _bullets(r.tool_calls), "", "</details>", "",
            "<details><summary>API calls</summary>", "", _bullets(r.api_calls), "", "</details>", "",
            "**Final state**", "", r.final_state or "-",
        ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    with httpx.Client(base_url=args.base_url, timeout=120, headers={"X-User": "scenario-runner"}) as client:
        health = client.get("/api/health").json()
        if health.get("weather_mode") != "simulation":
            print("WARNING: WEATHER_MODE is not 'simulation': TC-07 (weather injection) will fail.")
        results = []
        for fn in TEST_CASES:
            r = run_safely(fn, client)
            print(f"{r.tc}: {'PASS' if r.passed else 'FAIL'} ({sum(c[1] for c in r.checks)}/{len(r.checks)} checks)")
            for name, ok, detail in r.checks:
                if not ok:
                    print(f"    ✗ {name}: {detail}")
            results.append(r)
        client.post("/api/simulation/reset")  # leave the demo in a clean state

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(results, health, args.base_url))
    print(f"Wrote {args.out} ({sum(r.passed for r in results)}/{len(results)} passed)")


if __name__ == "__main__":
    main()
