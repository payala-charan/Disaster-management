"""Schemas for the JSON files in backend/data/seed/ and backend/data/scenarios/.

Validating data files with Pydantic means a typo in a JSON file fails loudly at load time
(and in the test suite) instead of producing a subtly wrong demo.

Timestamps in data files are RELATIVE ({"minutes_ago": 95} / {"minutes_ahead": 30}) and are
resolved when loaded, so the scenario always looks "live" no matter when it is seeded.
"""

from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from app.models.schemas import (
    ActionEventType,
    ActionStatus,
    Confidence,
    Contract,
    GeoPoint,
    IncidentSource,
    IncidentStatus,
    IncidentType,
    PriorityLevel,
    Provenance,
    ResourceStatus,
    ResourceType,
    RoadStatus,
    ShelterStatus,
    VerificationStatus,
)


class RelativeTime(Contract):
    minutes_ago: int | None = None
    minutes_ahead: int | None = None

    @model_validator(mode="after")
    def exactly_one(self) -> "RelativeTime":
        if (self.minutes_ago is None) == (self.minutes_ahead is None):
            raise ValueError("give exactly one of minutes_ago / minutes_ahead")
        return self

    def resolve(self, now: datetime) -> datetime:
        if self.minutes_ago is not None:
            return now - timedelta(minutes=self.minutes_ago)
        return now + timedelta(minutes=self.minutes_ahead or 0)


# ---------------------------------------------------------------- gazetteer

class Zone(Contract):
    name: str
    center: GeoPoint
    description: str
    aliases: list[str] = []


class ResponseBase(Contract):
    name: str
    location: GeoPoint


class Gazetteer(Contract):
    zones: list[Zone]
    bases: list[ResponseBase]


# ---------------------------------------------------------------- base seed entities

class SeedIncident(Contract):
    """A raw incident as reported. Deliberately has NO priority: the engine computes it."""

    id: str
    type: IncidentType
    title: str
    description: str
    zone: str
    location: GeoPoint | None
    reported_at: RelativeTime
    source: IncidentSource
    people_affected: int | None
    assistance_requested: list[str]
    infrastructure_issues: list[str]
    threat_indicators: list[str]
    confidence: Confidence
    verification_status: VerificationStatus
    status: IncidentStatus = "active"
    supporting_info: str | None = None
    field_provenance: dict[str, Provenance]


class SeedResource(Contract):
    id: str
    type: ResourceType
    name: str
    base_name: str
    location: GeoPoint
    status: ResourceStatus
    capacity: int = Field(ge=0)
    capabilities: list[str]
    expected_available_at: RelativeTime | None = None


class SeedShelter(Contract):
    id: str
    name: str
    location: GeoPoint
    capacity: int = Field(ge=0)
    current_occupancy: int = Field(ge=0)
    status: ShelterStatus


class SeedRoad(Contract):
    id: str
    name: str
    serves_zone: str
    status: RoadStatus
    provenance: Provenance
    path: list[GeoPoint] = Field(min_length=2)
    updated_at: RelativeTime
    note: str | None = None


class HourlyForecast(Contract):
    hour: int = Field(ge=1, le=6)
    precipitation_mm: float = Field(ge=0)
    wind_kph: float = Field(ge=0)


class SeedWeather(Contract):
    zone: str
    condition: str
    rainfall_mm_per_hr: float = Field(ge=0)
    wind_kph: float = Field(ge=0)
    temperature_c: float
    forecast_summary: str
    forecast_hourly: list[HourlyForecast]
    severe_warning: bool
    source: Literal["simulation"] = "simulation"
    fetched_at: RelativeTime


class SeedActionEvent(Contract):
    event: ActionEventType
    actor: str
    comment: str | None = None
    at: RelativeTime


class SeedAction(Contract):
    """Only actions that explain the seeded state (e.g. why BOAT-001 is already deployed)."""

    id: str
    incident_id: str
    resource_ids: list[str]
    description: str
    priority: PriorityLevel
    status: ActionStatus
    rationale: list[str]
    assumptions: list[str]
    inputs_used: list[dict[str, str]]
    created_at: RelativeTime
    approved_at: RelativeTime | None = None
    started_at: RelativeTime | None = None
    completed_at: RelativeTime | None = None
    due_by: RelativeTime | None = None
    events: list[SeedActionEvent]


class SeedChange(Contract):
    id: str
    entity: str
    entity_id: str | None
    field: str
    old_value: str
    new_value: str
    changed_at: RelativeTime
    provenance: Provenance


# ---------------------------------------------------------------- simulation scenarios

class _Step(Contract):
    at_minute: int = Field(ge=0, description="minutes after the scenario starts")
    note: str


class ReportStep(_Step):
    """Inject a raw report as one of the five source types (free text and/or structured payload)."""

    kind: Literal["report"]
    source: IncidentSource
    text: str | None = None
    payload: dict[str, Any] | None = None

    @model_validator(mode="after")
    def has_content(self) -> "ReportStep":
        if self.text is None and self.payload is None:
            raise ValueError("a report needs text and/or payload")
        return self


class RoadStep(_Step):
    kind: Literal["road"]
    road_id: str
    status: RoadStatus
    provenance: Provenance
    road_note: str | None = None


class WeatherStep(_Step):
    kind: Literal["weather"]
    zone: str
    condition: str
    rainfall_mm_per_hr: float = Field(ge=0)
    wind_kph: float = Field(ge=0)
    temperature_c: float
    forecast_summary: str
    forecast_hourly: list[HourlyForecast]
    severe_warning: bool


class ShelterStep(_Step):
    kind: Literal["shelter"]
    shelter_id: str
    current_occupancy: int = Field(ge=0)
    status: ShelterStatus | None = None


class ResourceStep(_Step):
    kind: Literal["resource"]
    resource_id: str
    status: ResourceStatus
    reason: str
    expected_available_in_min: int | None = None


class IncidentUpdateStep(_Step):
    """A new fact about an existing incident (e.g. a revised people count from the field)."""

    kind: Literal["incident_update"]
    incident_id: str
    changes: dict[str, Any]
    provenance: Provenance


# The "kind" field tells Pydantic which step model to use (a discriminated union).
ScenarioStep = Annotated[
    ReportStep | RoadStep | WeatherStep | ShelterStep | ResourceStep | IncidentUpdateStep,
    Field(discriminator="kind"),
]


class ScenarioSetup(Contract):
    """Extra registry entries a scenario adds on top of the base seed (never removes any)."""

    roads: list[SeedRoad] = []
    shelters: list[SeedShelter] = []
    resources: list[SeedResource] = []


class Scenario(Contract):
    id: str
    title: str
    description: str
    test_cases: list[str]
    setup: ScenarioSetup = ScenarioSetup()
    steps: list[ScenarioStep]
    expected: list[str]
