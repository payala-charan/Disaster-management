"""Transparent priority score (0-100). Deterministic: the LLM never sets or changes a score.

| Factor                | Max | Rule (highest matching tier wins) |
|-----------------------|-----|-----------------------------------|
| Threat to life        | 25  | trapped / stranded / injured / water_rescue -> 22; isolated / cut_off -> 18; evacuation_needed -> 12; property_damage_only -> 5; none -> 0 + "Confirm threat to life" |
| People affected       | 20  | unknown -> 0 + "Confirm number of people affected"; 0 -> 0; 1-9 -> 5; 10-24 -> 8; 25-49 -> 14; >=50 -> 20 |
| Access route          | 15  | best KNOWN route status for the zone (engines/access.py): open 0; partially_blocked 8; closed 15; unknown / only inferred 10 + "Route verification required" |
| Weather / escalation  | 15  | severe warning AND heavy rain forecast (>=10 mm/h within 6 h) -> 13; heavy rain now or forecast -> 10; moderate rain (>=2.5 mm/h) now or forecast -> 5; none -> 0; +2 if wind >= 50 km/h (cap 15); no weather data -> 0 + verification |
| Infrastructure impact | 10  | main road closed 8; road partially flooded 6; power/utility loss 6; any other reported issue 4; none 0 |
| Response gap          | 15  | nothing assigned 15; assigned units below the estimated requirement (engines/resources.py) for any type, or an unmet external need -> 8; adequately covered 0 |

Levels: Critical >= 75, High 55-74, Moderate 35-54, Low < 35.
The level is overridden to `verification_required` (the score is still shown) when the location is
unknown, OR confidence is low AND the report is unverified AND it is not linked to a verified incident.

Each factor records the text value and the provenance of its input. `reasons` are templated
sentences built from the highest-scoring factors; the LLM may rephrase them but may not add facts.

Seed scenario results (asserted in tests/engines/test_priority.py):
- INC-003: 18 + 20 + 15 + 13 + 8 + 15 = 89, Critical.
- INC-002: 22 + 8 + 0 + 13 + 0 + 15 = 58, High.
- INC-001: 12 + 14 + 8 + 13 + 6 + 8 = 61, High. The original UI mock showed 53 (Moderate) because it
  treated BOAT-001 + TEAM-001 as full coverage. By the documented rule, 40 people need 5 boats and
  2 teams while 1 of each is assigned, so the response gap is 8.
"""

from datetime import datetime

from app.engines.access import ZoneAccess
from app.engines.models import IncidentFacts, ResourceFacts, WeatherFacts
from app.engines.resources import CONFIRM_PEOPLE, RequirementEstimate
from app.models.schemas import PriorityAssessment, PriorityFactor, PriorityLevel

HEAVY_RAIN_MM_PER_HR = 10.0
MODERATE_RAIN_MM_PER_HR = 2.5
STRONG_WIND_KPH = 50.0

# Controlled vocabulary for threat indicators (the LLM classifier must output these labels).
THREAT_TIERS: list[tuple[int, set[str], str]] = [
    (22, {"trapped", "stranded", "injured", "water_rescue"}, "Immediate threat to life"),
    (18, {"isolated", "cut_off"}, "Community isolated"),
    (12, {"evacuation_needed"}, "Evacuation needed"),
    (5, {"property_damage_only"}, "Property damage only"),
]
CONFIRM_THREAT = "Confirm threat to life"

ACCESS_POINTS = {"open": 0, "partially_blocked": 8, "closed": 15, "unknown": 10}
INFRA_TIERS: list[tuple[int, set[str]]] = [
    (8, {"main_road_closed"}),
    (6, {"road_partially_flooded", "power_outage", "power_loss", "utility_loss", "water_supply_loss"}),
]


def level_for(score: int) -> PriorityLevel:
    if score >= 75:
        return "critical"
    if score >= 55:
        return "high"
    if score >= 35:
        return "moderate"
    return "low"


def _threat(inc: IncidentFacts, verify: list[str]) -> PriorityFactor:
    found = set(inc.threat_indicators)
    for points, labels, text in THREAT_TIERS:
        hit = sorted(found & labels)
        if hit:
            return PriorityFactor(name="Threat to life", value=f"{text} ({', '.join(hit)})", points=points,
                                  max_points=25, provenance=inc.provenance("threat_indicators"))
    verify.append(CONFIRM_THREAT)
    return PriorityFactor(name="Threat to life", value="No threat-to-life information", points=0,
                          max_points=25, provenance=inc.provenance("threat_indicators"))


def _people(inc: IncidentFacts, verify: list[str]) -> PriorityFactor:
    n = inc.people_affected
    if n is None:
        verify.append(CONFIRM_PEOPLE)
        return PriorityFactor(name="People affected", value="Not reported (verification required)", points=0,
                              max_points=20, provenance="reported")
    points = 0 if n == 0 else 5 if n < 10 else 8 if n < 25 else 14 if n < 50 else 20
    return PriorityFactor(name="People affected", value=f"{n} reported", points=points, max_points=20,
                          provenance=inc.provenance("people_affected"))


def _access(access: ZoneAccess, verify: list[str]) -> PriorityFactor:
    verify.extend(access.verification_needed)  # includes "Route verification required" when unknown
    if access.status == "unknown":
        value = f"No confirmed route to {access.zone}"
    else:
        value = f"{access.deciding_road_name} {access.status.replace('_', ' ')}"
    return PriorityFactor(name="Access route", value=value, points=ACCESS_POINTS[access.status], max_points=15,
                          provenance=access.provenance)


def _weather(w: WeatherFacts | None, verify: list[str]) -> PriorityFactor:
    if w is None:
        verify.append("Weather data unavailable")
        return PriorityFactor(name="Weather / escalation", value="No weather data", points=0, max_points=15,
                              provenance="inferred")
    peak = max(w.rainfall_mm_per_hr, w.forecast_max_mm_per_hr)
    if w.severe_warning and w.forecast_max_mm_per_hr >= HEAVY_RAIN_MM_PER_HR:
        points, value = 13, f"Severe warning; heavy rain up to {w.forecast_max_mm_per_hr:g} mm/h in next 6 h"
    elif peak >= HEAVY_RAIN_MM_PER_HR:
        points, value = 10, f"Heavy rain ({peak:g} mm/h)"
    elif peak >= MODERATE_RAIN_MM_PER_HR:
        points, value = 5, f"Moderate rain ({peak:g} mm/h)"
    else:
        points, value = 0, "No significant rain"
    if w.wind_kph >= STRONG_WIND_KPH:
        points, value = min(15, points + 2), f"{value}; strong wind {w.wind_kph:g} km/h"
    return PriorityFactor(name="Weather / escalation", value=value, points=points, max_points=15, provenance="known")


def _infrastructure(inc: IncidentFacts) -> PriorityFactor:
    issues = set(inc.infrastructure_issues)
    prov = inc.provenance("infrastructure_issues")
    for points, labels in INFRA_TIERS:
        hit = sorted(issues & labels)
        if hit:
            return PriorityFactor(name="Infrastructure impact", value=", ".join(hit).replace("_", " "),
                                  points=points, max_points=10, provenance=prov)
    if issues:
        return PriorityFactor(name="Infrastructure impact", value=", ".join(sorted(issues)).replace("_", " "),
                              points=4, max_points=10, provenance=prov)
    return PriorityFactor(name="Infrastructure impact", value="None reported", points=0, max_points=10,
                          provenance=prov)


def _response_gap(req: RequirementEstimate, assigned: list[ResourceFacts]) -> PriorityFactor:
    if not assigned:
        return PriorityFactor(name="Response gap", value="No resources assigned", points=15, max_points=15,
                              provenance="known")
    ids = ", ".join(r.id for r in assigned)
    short = [
        f"{item.resource_type} {sum(r.type == item.resource_type for r in assigned)}/{item.units}"
        for item in req.items
        if sum(r.type == item.resource_type for r in assigned) < item.units
    ]
    if short or req.external_needs:
        detail = "; ".join(short + (["external need unmet"] if req.external_needs else []))
        return PriorityFactor(name="Response gap", value=f"{ids} assigned, below requirement ({detail})",
                              points=8, max_points=15, provenance="known")
    return PriorityFactor(name="Response gap", value=f"{ids} assigned, requirement covered", points=0,
                          max_points=15, provenance="known")


def _reasons(factors: list[PriorityFactor]) -> list[str]:
    """Templated sentences from factors scoring at least half their maximum, highest first."""
    top = sorted((f for f in factors if f.points * 2 >= f.max_points and f.points > 0),
                 key=lambda f: (-f.points / f.max_points, -f.points))
    return [f"{f.name}: {f.value} ({f.points}/{f.max_points})" for f in top]


def assess_priority(
    incident: IncidentFacts,
    access: ZoneAccess,
    weather: WeatherFacts | None,
    requirements: RequirementEstimate,
    assigned: list[ResourceFacts],
    now: datetime,
) -> PriorityAssessment:
    verify: list[str] = []
    factors = [
        _threat(incident, verify),
        _people(incident, verify),
        _access(access, verify),
        _weather(weather, verify),
        _infrastructure(incident),
        _response_gap(requirements, assigned),
    ]
    score = sum(f.points for f in factors)
    level = level_for(score)
    reasons = _reasons(factors)

    if incident.location is None:
        level = "verification_required"
        verify.insert(0, "Location unknown: confirm incident location")
    elif (incident.confidence == "low" and incident.verification_status == "unverified"
          and not incident.linked_to_verified):
        level = "verification_required"
        verify.insert(0, "Low-confidence unverified report: verify before acting")
    if level == "verification_required":
        reasons.insert(0, f"Score {score} computed, but level held at Verification Required until confirmed")

    # Keep order, drop duplicates (the same item can come from several factors).
    verify = list(dict.fromkeys(verify + [v for v in requirements.verification_needed if v not in verify]))
    return PriorityAssessment(score=score, level=level, factors=factors, reasons=reasons,
                              verification_needed=verify, assessed_at=now)
