"""Agent 3: Weather & Environmental Intelligence.

For every zone with an active incident: get the current reading (Open-Meteo or simulation, via the
weather tool), store new live readings, and compare the newest snapshot with the previous one.
If the newest snapshot arrived since the last completed run and is worse (engines/replanning.py:
weather_worsened), a weather_worsening alert and a condition change are emitted. That is what makes
the Severity agent reassess with the new data.
"""

from typing import Any

from app.agents.base import Deps, StepRecorder, agent
from app.db import repository as repo
from app.engines.replanning import weather_worsened


@agent("weather_intelligence")
def weather_intelligence(state: dict[str, Any], deps: Deps, rec: StepRecorder, db) -> dict[str, Any]:
    zones = sorted({i["zone"] for i in state.get("incidents", {}).values()})
    centers = {z.name: z.center for z in repo.gazetteer().zones}
    since = repo.previous_run_started(db, state["run_id"])
    rec.input_summary = f"Zones with active incidents: {', '.join(zones) or 'none'} (mode {deps.settings.weather_mode})"

    weather: dict[str, Any] = {}
    alerts, changes, missing = [], [], []
    for zone in zones:
        if zone not in centers:
            missing.append(zone)
            continue
        reading = rec.tool(
            "weather.get", {"zone": zone, "mode": deps.settings.weather_mode},
            lambda: deps.weather.get(zone, centers[zone]),
            lambda r: (f"{r.source}: {r.condition}, {r.rainfall_mm_per_hr:g} mm/h, wind {r.wind_kph:g} km/h, "
                       f"severe={r.severe_warning}" + (f" [{r.fallback_reason}]" if r.fallback_reason else ""))
            if r else "no data",
        )
        if reading is None:
            missing.append(zone)
            continue
        latest = repo.weather_history(db, zone, limit=1)
        if reading.source == "open-meteo" and (not latest or reading.fetched_at > latest[0].fetched_at):
            repo.insert_weather(db, reading)
            db.flush()

        history = repo.weather_history(db, zone, limit=2)
        newest, previous = history[0], (history[1] if len(history) > 1 else None)
        if (since is None or newest.fetched_at > since) and weather_worsened(previous.facts() if previous else None,
                                                                           newest.facts()):
            alerts.append({"kind": "weather_worsening", "severity": "warning", "related_id": None,
                           "message": f"{'Weather worsening' if previous else 'Severe weather'} in {zone}: {newest.condition}, "
                                      f"{newest.rainfall_mm_per_hr:g} mm/h, wind {newest.wind_kph:g} km/h. "
                                      f"{newest.forecast_summary}"})
            changes.append({"entity": f"Weather {zone}", "entity_id": None, "field": "conditions",
                            "old_value": (f"{previous.condition}, {previous.rainfall_mm_per_hr:g} mm/h"
                                          if previous else "no previous reading"),
                            "new_value": f"{newest.condition}, {newest.rainfall_mm_per_hr:g} mm/h", "provenance": "known"})
        weather[zone] = reading.facts().model_dump(mode="json")

    rec.output_summary = (f"Weather for {len(weather)} zone(s); worsening in {len(alerts)}; "
                          f"no data for {missing or 'none'}")
    return {"weather": weather, "alerts_to_emit": alerts, "changes": changes}
