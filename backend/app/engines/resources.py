"""Resource lifecycle (state machine) and resource-requirement estimation.

## State machine
    available -> reserved      (coordinator approves an action)
    reserved  -> deployed      (action started)
    reserved  -> available     (approval cancelled)
    deployed  -> returning     (task finished, heading back)
    returning -> available     (back at base)
    any       -> unavailable   (breakdown, crew off duty, ...)
    unavailable -> available   (back in service)
Anything else (including X -> X) raises InvalidTransition. The API turns that into HTTP 409.

## Requirement estimation (every number carries its formula)
Let n = people_affected.
- Water rescue is needed when assistance includes water_rescue or evacuation AND
  (the incident is a flood whose known access is closed/partially blocked, OR people are stranded).
    rescue_boat = ceil(n / 8)           (nominal boat capacity 8)
    rescue_team = 1 + floor(n / 30)
- Otherwise, if evacuation is requested, it is by road:
    emergency_vehicle = ceil(n / 10)    (nominal vehicle capacity 10)
    rescue_team       = 1 + floor(n / 30)
- People trapped (landslide, collapse, fire) need search and rescue:
    rescue_team = 1 + floor(n / 30)     (the larger of this and any value above)
- Medical help requested (medical_check / medical / first_aid / medical_transport) or people injured:
    ambulance = max(1, ceil(n x 0.05))
- Fire needs fire-fighting units. The registry has none, so this is reported as an EXTERNAL need
  ("request fire services") and is never turned into an invented resource.
- n unknown: each needed type gets the minimum of 1 unit, marked verification_required, and
  "Confirm number of people affected" is added. The count is never guessed.
"""

import math

from app.engines.access import ZoneAccess
from app.engines.models import IncidentFacts
from app.models.schemas import Contract, ResourceStatus, ResourceType

# ---------------------------------------------------------------- state machine

ALLOWED_TRANSITIONS: dict[ResourceStatus, set[ResourceStatus]] = {
    "available": {"reserved", "unavailable"},
    "reserved": {"deployed", "available", "unavailable"},
    "deployed": {"returning", "unavailable"},
    "returning": {"available", "unavailable"},
    "unavailable": {"available"},
}


class InvalidTransition(ValueError):
    pass


def allowed_transitions(current: ResourceStatus) -> list[ResourceStatus]:
    """Valid next states, e.g. for the UI to offer only legal buttons."""
    return sorted(ALLOWED_TRANSITIONS[current])


def validate_transition(current: ResourceStatus, new: ResourceStatus) -> None:
    if new not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransition(
            f"Cannot change status from '{current}' to '{new}'. Allowed: {allowed_transitions(current) or 'none'}"
        )


# ---------------------------------------------------------------- requirements

BOAT_CAPACITY = 8
VEHICLE_CAPACITY = 10
PEOPLE_PER_EXTRA_TEAM = 30
AMBULANCE_RATE = 0.05

WATER_ASSISTANCE = {"water_rescue", "evacuation"}
MEDICAL_ASSISTANCE = {"medical_check", "medical", "first_aid", "medical_transport"}
CONFIRM_PEOPLE = "Confirm number of people affected"


class RequirementItem(Contract):
    resource_type: ResourceType
    units: int
    formula: str
    verification_required: bool


class RequirementEstimate(Contract):
    incident_id: str
    items: list[RequirementItem]
    external_needs: list[str]        # needs no registry resource can meet (e.g. fire services)
    verification_needed: list[str]

    def units(self, resource_type: ResourceType) -> int:
        return sum(i.units for i in self.items if i.resource_type == resource_type)


def estimate_requirements(incident: IncidentFacts, access: ZoneAccess) -> RequirementEstimate:
    n = incident.people_affected
    unknown = n is None
    assistance = set(incident.assistance_requested)
    indicators = set(incident.threat_indicators)
    items: dict[ResourceType, RequirementItem] = {}
    verification: list[str] = []
    external: list[str] = []

    def need(rtype: ResourceType, units_if_known: int, formula: str) -> None:
        if unknown:
            item = RequirementItem(
                resource_type=rtype, units=1, verification_required=True,
                formula="people count unknown -> minimum 1 unit (verification required)",
            )
        else:
            item = RequirementItem(resource_type=rtype, units=units_if_known, formula=formula, verification_required=False)
        current = items.get(rtype)
        if (current is None or item.units > current.units) and item.units > 0:
            items[rtype] = item

    teams = 1 + (n or 0) // PEOPLE_PER_EXTRA_TEAM
    team_formula = f"1 + floor({n} people / {PEOPLE_PER_EXTRA_TEAM}) = {teams}"

    water_context = incident.type == "flood" and access.status in ("closed", "partially_blocked")
    if assistance & WATER_ASSISTANCE and (water_context or "stranded" in indicators):
        boats = math.ceil((n or 0) / BOAT_CAPACITY)
        need("rescue_boat", boats, f"ceil({n} people / {BOAT_CAPACITY} per boat) = {boats}")
        need("rescue_team", teams, team_formula)
    elif "evacuation" in assistance:
        vehicles = math.ceil((n or 0) / VEHICLE_CAPACITY)
        need("emergency_vehicle", vehicles, f"ceil({n} people / {VEHICLE_CAPACITY} per vehicle) = {vehicles}")
        need("rescue_team", teams, team_formula)

    if "trapped" in indicators:
        need("rescue_team", teams, f"search and rescue: {team_formula}")

    if assistance & MEDICAL_ASSISTANCE or "injured" in indicators:
        ambulances = max(1, math.ceil((n or 0) * AMBULANCE_RATE))
        need("ambulance", ambulances, f"max(1, ceil({n} people x {AMBULANCE_RATE})) = {ambulances}")

    if "fire" in indicators or "fire_suppression" in assistance or incident.type == "wildfire":
        external.append("Fire suppression: no fire-fighting units in this registry. Request fire services.")

    if unknown and items:
        verification.append(CONFIRM_PEOPLE)
    if not items and not external:
        verification.append("Assistance needs unclear: confirm what help is required")

    return RequirementEstimate(
        incident_id=incident.id,
        items=sorted(items.values(), key=lambda i: i.resource_type),
        external_needs=external,
        verification_needed=verification,
    )
