"""Build engine inputs from the seed JSON (the same data the demo uses), with a fixed 'now'."""

from datetime import datetime, timezone

import pytest

from app.db.seed import SeedBundle, load_seed
from app.engines.access import ZoneAccess, assess_zone_access
from app.engines.allocation import AllocationInput
from app.engines.models import IncidentFacts, ResourceFacts, RoadFacts, WeatherFacts
from app.engines.priority import assess_priority
from app.engines.resources import estimate_requirements

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def incident_facts(b: SeedBundle) -> dict[str, IncidentFacts]:
    verified = {i.id for i in b.incidents if i.verification_status == "verified"}
    out = {}
    for i in b.incidents:
        # INC-004 is the citizen duplicate of INC-002 in the scenario (the link itself is made in Phase 4).
        dup = "INC-002" if i.id == "INC-004" else None
        out[i.id] = IncidentFacts(
            id=i.id, type=i.type, title=i.title, description=i.description, zone=i.zone, location=i.location,
            reported_at=i.reported_at.resolve(NOW), people_affected=i.people_affected,
            assistance_requested=i.assistance_requested, infrastructure_issues=i.infrastructure_issues,
            threat_indicators=i.threat_indicators, confidence=i.confidence,
            verification_status=i.verification_status, field_provenance=i.field_provenance,
            linked_to_verified=dup in verified, duplicate_of=dup,
        )
    return out


def road_facts(b: SeedBundle) -> list[RoadFacts]:
    return [RoadFacts(id=r.id, name=r.name, serves_zone=r.serves_zone, status=r.status,
                      provenance=r.provenance, note=r.note) for r in b.roads]


def weather_facts(b: SeedBundle) -> dict[str, WeatherFacts]:
    return {w.zone: WeatherFacts.from_hourly(
        hourly=[h.precipitation_mm for h in w.forecast_hourly], zone=w.zone, condition=w.condition,
        rainfall_mm_per_hr=w.rainfall_mm_per_hr, wind_kph=w.wind_kph, severe_warning=w.severe_warning,
        source=w.source) for w in b.weather}


def resource_facts(b: SeedBundle) -> list[ResourceFacts]:
    assigned = {rid: a.incident_id for a in b.actions if a.status in ("approved", "in_progress") for rid in a.resource_ids}
    return [ResourceFacts(id=r.id, type=r.type, name=r.name, base_name=r.base_name, location=r.location,
                          status=r.status, capacity=r.capacity, capabilities=r.capabilities,
                          assigned_incident_id=assigned.get(r.id)) for r in b.resources]


class Scenario:
    """The seed scenario run through access -> requirements -> priority, ready for allocation."""

    def __init__(self, b: SeedBundle):
        self.incidents = incident_facts(b)
        self.roads = road_facts(b)
        self.weather = weather_facts(b)
        self.resources = resource_facts(b)

    def access(self, zone: str) -> ZoneAccess:
        return assess_zone_access(zone, self.roads)

    def assigned(self, incident_id: str) -> list[ResourceFacts]:
        return [r for r in self.resources if r.assigned_incident_id == incident_id]

    def allocation_input(self, incident_id: str) -> AllocationInput:
        inc = self.incidents[incident_id]
        access = self.access(inc.zone)
        req = estimate_requirements(inc, access)
        prio = assess_priority(inc, access, self.weather.get(inc.zone), req, self.assigned(incident_id), NOW)
        return AllocationInput(incident=inc, priority=prio, requirements=req, access=access,
                               assigned=self.assigned(incident_id))

    def allocation_inputs(self) -> list[AllocationInput]:
        return [self.allocation_input(i) for i in self.incidents]


@pytest.fixture(scope="session")
def bundle() -> SeedBundle:
    return load_seed()


@pytest.fixture
def scenario(bundle) -> Scenario:
    return Scenario(bundle)
