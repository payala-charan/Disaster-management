# Simulation scenarios

These files extend the base seed (`../seed/`) for the live demo and the TC-01…TC-08 tests.
Each one is validated by `app.models.seed_data.Scenario` (see `tests/test_seed_data.py`).

## File format

```jsonc
{
  "id": "...", "title": "...", "description": "...",
  "test_cases": ["TC-05"],                 // which test cases it supports
  "setup": { "roads": [], "shelters": [], "resources": [] },  // registry entries ADDED on top of the seed
  "steps": [                               // a timeline, replayed in order of at_minute
    { "at_minute": 0, "kind": "road", "note": "why this step exists", ... }
  ],
  "expected": ["what a correct system does"]  // the acceptance criteria
}
```

Step kinds:

| kind | Fields | Simulates |
|---|---|---|
| `report` | `source`, `text` and/or `payload` | a raw report from one of the 5 source types |
| `road` | `road_id`, `status`, `provenance`, `road_note` | a road registry update |
| `weather` | `zone`, readings, `forecast_hourly`, `severe_warning` | a new weather snapshot (simulation mode) |
| `shelter` | `shelter_id`, `current_occupancy`, `status?` | an occupancy update |
| `resource` | `resource_id`, `status`, `reason`, `expected_available_in_min?` | a resource status change |
| `incident_update` | `incident_id`, `changes`, `provenance` | new information about an existing incident |

Timestamps in setup entities are relative (`{"minutes_ago": 30}`), like the base seed.

The expected outcomes deliberately avoid hard-coding engine numbers (scores, deficits) where those come
from Phase 2 rules. The tests compute the expected values from the documented rules.

## Scenarios

| File | Test cases | What it covers |
|---|---|---|
| *(base seed)* | TC-03 | Zone A/B/C floods. INC-003 is critical, with boats available → allocation recommendation pending approval |
| `source_reports.json` | TC-01, TC-02 | All five source formats: free-text call, vague citizen message, structured field update, IoT sensor event, government alert. Two reports describe the same River Street flood (Zone D) → duplicate link for review |
| `multi_hazard.json` | TC-01, TC-04 | Landslide (Zone E, trapped people, road closed by debris) and an urban fire (Zone F, mapped to `urban_emergency`, unknown people count). Fire-fighting units are not in the registry, so the plan must say so and not invent one. Adds roads RD-E/RD-F, shelter SHL-E and medical teams MED-001/002 |
| `weather_timeline.json` | TC-07 | Zone C, then Zones A and B worsen (rain ≥ 29 mm/h, wind ≥ 50 km/h), then Zone C eases. Reassessment and alerts on worsening only |
| `road_changes.json` | TC-05 | RD-B (the only route to Zone B) closes after a recommendation. The inferred RD-C2 is confirmed closed (inferred → known). RD-A reopens |
| `shortage.json` | TC-04 | BOAT-004/005 out of service and INC-003 rises to 120 people → exact deficit and escalation note, no invented resources |
| `double_allocation.json` | TC-08 | Only BOAT-005 remains while Zone B and Zone C both need boats. The engine assigns it once per plan, and the DB rejects a second reservation (409) |
| `severity_escalation.json` | TC-07 | INC-001 gains people and injuries, then RD-A closes. The level rises and a `severity_increase` alert fires |
| `shelter_pressure.json` | TC-06 | Shelter A goes 84% → 86% → full. The alert fires exactly when the threshold is crossed |

## Geography

The district is fictional ("Example District"). Zones A–C come from the original frontend mock. Zones D–F
exist in the gazetteer (`../seed/zones.json`) and only receive incidents through scenarios.
