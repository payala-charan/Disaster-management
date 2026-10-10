"""Impact analysis for a changed condition. Pure: it only reports what is affected; the replanning
service (Phase 5) records the change, supersedes actions, raises alerts and starts a new run.

Rules:
- Road change -> incidents in the road's zone are reassessed (the access factor depends on it).
- Weather change -> incidents in that zone are reassessed. The caller sets `worsened` using
  weather_worsened(old, new): a severe warning appears, current or forecast peak rain rises by
  >= 5 mm/h, or wind crosses 50 km/h.
- Resource becomes unavailable -> actions that include it are affected; their incidents are reassessed.
  Resource becomes available -> every active incident with a pending action is reassessed (new capacity).
- Shelter change -> only when a shelter stops accepting people (full/closed) are incidents that
  requested evacuation reassessed.
- Incident update -> that incident is reassessed.

Action handling (human-in-the-loop is preserved):
- `pending` actions of affected incidents are SUPERSEDED: a fresh recommendation replaces them.
- `approved` / `in_progress` actions are never changed automatically. When the change worsens their
  conditions they are listed for coordinator REVIEW.
"""

from typing import Literal

from app.engines.models import WeatherFacts
from app.engines.priority import STRONG_WIND_KPH
from app.models.schemas import ActionStatus, Contract, RoadStatus

ChangeKind = Literal["road", "weather", "resource", "shelter", "incident"]

_ROAD_RANK: dict[RoadStatus, int] = {"open": 0, "partially_blocked": 1, "unknown": 2, "closed": 3}
RAIN_WORSENING_MM_PER_HR = 5.0


class ConditionChangeEvent(Contract):
    kind: ChangeKind
    entity_id: str          # RD-B, Zone C, BOAT-002, SHL-A, INC-001
    zone: str | None = None  # for road and weather changes
    old_value: str | None = None
    new_value: str | None = None
    worsened: bool = False   # caller may set it; road/resource changes are derived below


class IncidentRef(Contract):
    id: str
    zone: str
    status: str = "active"
    evacuation_requested: bool = False


class ActionRef(Contract):
    id: str
    incident_id: str
    resource_ids: list[str]
    status: ActionStatus


class ReplanImpact(Contract):
    change: ConditionChangeEvent
    worsened: bool
    reassess_incident_ids: list[str]
    supersede_action_ids: list[str]
    review_action_ids: list[str]
    trigger_run: bool
    reasons: list[str]


def road_worsened(old: RoadStatus, new: RoadStatus) -> bool:
    return _ROAD_RANK[new] > _ROAD_RANK[old]


def weather_worsened(old: WeatherFacts | None, new: WeatherFacts) -> bool:
    if old is None:
        return new.severe_warning
    old_peak = max(old.rainfall_mm_per_hr, old.forecast_max_mm_per_hr)
    new_peak = max(new.rainfall_mm_per_hr, new.forecast_max_mm_per_hr)
    return (
        (new.severe_warning and not old.severe_warning)
        or new_peak - old_peak >= RAIN_WORSENING_MM_PER_HR
        or (old.wind_kph < STRONG_WIND_KPH <= new.wind_kph)
    )


def analyze_change(
    change: ConditionChangeEvent, incidents: list[IncidentRef], actions: list[ActionRef],
) -> ReplanImpact:
    active = [i for i in incidents if i.status != "resolved"]
    open_actions = [a for a in actions if a.status in ("pending", "approved", "in_progress")]
    worsened = change.worsened
    reasons: list[str] = []
    affected: set[str] = set()

    match change.kind:
        case "road":
            if change.old_value and change.new_value:
                worsened = road_worsened(change.old_value, change.new_value)  # type: ignore[arg-type]
            affected = {i.id for i in active if i.zone == change.zone}
            reasons.append(f"{change.entity_id} changed {change.old_value} -> {change.new_value}; "
                           f"access to {change.zone} must be reassessed")
        case "weather":
            affected = {i.id for i in active if i.zone == change.zone}
            reasons.append(f"Weather in {change.zone} {'worsened' if worsened else 'changed'}")
        case "resource":
            if change.new_value == "unavailable":
                worsened = True
                hit = [a for a in open_actions if change.entity_id in a.resource_ids]
                affected = {a.incident_id for a in hit}
                reasons.append(f"{change.entity_id} is no longer available")
            elif change.new_value == "available":
                affected = {a.incident_id for a in open_actions if a.status == "pending"}
                reasons.append(f"{change.entity_id} became available: pending recommendations may improve")
        case "shelter":
            if change.new_value in ("full", "closed"):
                worsened = True
                affected = {i.id for i in active if i.evacuation_requested}
                reasons.append(f"{change.entity_id} is {change.new_value}: evacuation destinations must be reassessed")
        case "incident":
            affected = {change.entity_id}
            reasons.append(f"New information about {change.entity_id}")

    affected_actions = [a for a in open_actions if a.incident_id in affected]
    if change.kind == "resource" and change.new_value == "unavailable":
        affected_actions = [a for a in open_actions if change.entity_id in a.resource_ids]

    supersede = sorted(a.id for a in affected_actions if a.status == "pending")
    review = sorted(a.id for a in affected_actions if a.status in ("approved", "in_progress")) if worsened else []
    if review:
        reasons.append(f"Approved/in-progress actions need coordinator review: {', '.join(review)}")

    return ReplanImpact(
        change=change, worsened=worsened, reassess_incident_ids=sorted(affected),
        supersede_action_ids=supersede, review_action_ids=review,
        trigger_run=bool(affected), reasons=reasons,
    )
