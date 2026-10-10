"""Input models for the deterministic engines.

Engines never touch the database or the network: callers (agents, services, tests) build these
plain objects first. That is what makes every rule unit-testable in isolation.
"""

from datetime import datetime

from app.models.schemas import (
    Confidence,
    Contract,
    GeoPoint,
    IncidentType,
    Provenance,
    ResourceStatus,
    ResourceType,
    RoadStatus,
    VerificationStatus,
)


class IncidentFacts(Contract):
    id: str
    type: IncidentType
    title: str
    description: str
    zone: str
    location: GeoPoint | None  # None = unknown location -> "verification_required"
    reported_at: datetime
    people_affected: int | None
    assistance_requested: list[str] = []
    infrastructure_issues: list[str] = []
    threat_indicators: list[str] = []
    confidence: Confidence
    verification_status: VerificationStatus
    field_provenance: dict[str, Provenance] = {}
    linked_to_verified: bool = False  # linked (duplicate/related) to a verified incident
    duplicate_of: str | None = None   # pending/confirmed potential duplicate of another incident

    def provenance(self, field: str) -> Provenance:
        """Facts from a report are 'reported' unless recorded otherwise."""
        return self.field_provenance.get(field, "reported")


class RoadFacts(Contract):
    id: str
    name: str
    serves_zone: str
    status: RoadStatus
    provenance: Provenance
    note: str | None = None


class WeatherFacts(Contract):
    zone: str
    condition: str
    rainfall_mm_per_hr: float
    wind_kph: float
    severe_warning: bool
    forecast_max_mm_per_hr: float  # highest hourly precipitation in the next 6 h
    source: str

    @classmethod
    def from_hourly(cls, *, hourly: list[float], **kwargs) -> "WeatherFacts":
        return cls(forecast_max_mm_per_hr=max(hourly, default=0.0), **kwargs)


class ResourceFacts(Contract):
    id: str
    type: ResourceType
    name: str
    base_name: str
    location: GeoPoint
    status: ResourceStatus
    capacity: int
    capabilities: list[str] = []
    assigned_incident_id: str | None = None
