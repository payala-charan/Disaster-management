"""Database access shared by agents and API services: rows in, engine facts / API schemas out.

Keeping every query here means agents read like business logic and the SQL stays in one place.
"""

from datetime import datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import Integer, cast, func, select, text
from sqlalchemy.orm import Session

from app.db import models as m
from app.db.geo import geom_to_point
from app.engines.models import IncidentFacts, ResourceFacts, RoadFacts
from app.integrations.weather import WeatherReading
from app.models.schemas import Shelter
from app.models.seed_data import Gazetteer, HourlyForecast

ACTIVE_ASSIGNMENT = ("reserved", "deployed")
OPEN_ACTION = ("pending", "approved", "in_progress")


# ---------------------------------------------------------------- IDs

def next_id(db: Session, model: type[m.Base], prefix: str, width: int = 3) -> str:
    """Next human-readable ID (INC-005, ...). A transaction-scoped advisory lock serialises callers
    per prefix, so two concurrent requests can never generate the same ID. Pending objects are
    flushed first so IDs created earlier in this transaction are counted."""
    db.flush()
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:p))"), {"p": prefix})
    number = cast(func.substring(model.id, "[0-9]+$"), Integer)
    current = db.scalar(select(func.max(number)).where(model.id.like(f"{prefix}-%")))
    return f"{prefix}-{(current or 0) + 1:0{width}d}"


@lru_cache
def gazetteer() -> Gazetteer:
    from app.db.seed import load_seed

    return load_seed().gazetteer


# ---------------------------------------------------------------- incidents & duplicates

def _link_maps(db: Session) -> tuple[dict[str, str], dict[str, set[str]]]:
    """(duplicate_of per newer incident, corroborating incident IDs per incident) from non-rejected
    potential_duplicate links. 'related' links are shown for review but do NOT corroborate: two reports
    of the same type at the same time in different zones are not evidence for each other."""
    links = db.scalars(select(m.DuplicateLink).where(m.DuplicateLink.status != "rejected")
                       .order_by(m.DuplicateLink.score.desc())).all()
    duplicate_of: dict[str, str] = {}
    linked: dict[str, set[str]] = {}
    for link in links:
        if link.classification != "potential_duplicate":
            continue
        linked.setdefault(link.incident_id, set()).add(link.candidate_id)
        linked.setdefault(link.candidate_id, set()).add(link.incident_id)
        duplicate_of.setdefault(link.incident_id, link.candidate_id)  # highest score wins
    return duplicate_of, linked


def incident_facts(db: Session, include_resolved: bool = False) -> dict[str, IncidentFacts]:
    query = select(m.Incident).order_by(m.Incident.id)
    if not include_resolved:
        query = query.where(m.Incident.status != "resolved")
    rows = db.scalars(query).all()
    duplicate_of, linked = _link_maps(db)
    verified = set(db.scalars(select(m.Incident.id).where(m.Incident.verification_status == "verified")))
    return {
        r.id: IncidentFacts(
            id=r.id, type=r.type, title=r.title, description=r.description, zone=r.zone,
            location=geom_to_point(r.geom), reported_at=r.reported_at, people_affected=r.people_affected,
            assistance_requested=r.assistance_requested, infrastructure_issues=r.infrastructure_issues,
            threat_indicators=r.threat_indicators, confidence=r.confidence,
            verification_status=r.verification_status, field_provenance=r.field_provenance,
            linked_to_verified=bool(linked.get(r.id, set()) & verified),
            duplicate_of=duplicate_of.get(r.id),
        )
        for r in rows
    }


# ---------------------------------------------------------------- registry

def road_facts(db: Session) -> list[RoadFacts]:
    return [RoadFacts(id=r.id, name=r.name, serves_zone=r.serves_zone, status=r.status, provenance=r.provenance,
                      note=r.note) for r in db.scalars(select(m.RoadSegment).order_by(m.RoadSegment.id))]


def active_assignments(db: Session) -> dict[str, str]:
    """resource_id -> incident_id for reserved/deployed assignments."""
    rows = db.execute(select(m.ResourceAssignment.resource_id, m.ResourceAssignment.incident_id)
                      .where(m.ResourceAssignment.status.in_(ACTIVE_ASSIGNMENT))).all()
    return dict(rows)


def resource_facts(db: Session) -> list[ResourceFacts]:
    assigned = active_assignments(db)
    return [ResourceFacts(id=r.id, type=r.type, name=r.name, base_name=r.base_name, location=geom_to_point(r.geom),
                          status=r.status, capacity=r.capacity, capabilities=r.capabilities,
                          assigned_incident_id=assigned.get(r.id))
            for r in db.scalars(select(m.Resource).order_by(m.Resource.id))]


def shelters(db: Session) -> list[Shelter]:
    return [Shelter(id=s.id, name=s.name, location=geom_to_point(s.geom), capacity=s.capacity,
                    current_occupancy=s.current_occupancy, status=s.status)
            for s in db.scalars(select(m.Shelter).order_by(m.Shelter.id))]


def nearest_shelters(db: Session, incident_id: str, limit: int = 3) -> list[dict[str, Any]]:
    """PostGIS: shelters ordered by great-circle distance (metres) from the incident."""
    inc_geom = select(m.Incident.geom).where(m.Incident.id == incident_id).scalar_subquery()
    distance = func.ST_DistanceSphere(m.Shelter.geom, inc_geom)
    rows = db.execute(select(m.Shelter.id, m.Shelter.name, m.Shelter.capacity, m.Shelter.current_occupancy,
                             m.Shelter.status, distance.label("metres"))
                      .where(inc_geom.is_not(None)).order_by(distance).limit(limit)).all()
    return [{"shelter_id": r.id, "name": r.name, "available": max(0, r.capacity - r.current_occupancy),
             "status": r.status, "distance_m": round(r.metres)} for r in rows if r.metres is not None]


# ---------------------------------------------------------------- weather

def _reading(row: m.WeatherSnapshot) -> WeatherReading:
    return WeatherReading(
        zone=row.zone, condition=row.condition, rainfall_mm_per_hr=row.rainfall_mm_per_hr, wind_kph=row.wind_kph,
        temperature_c=row.temperature_c, forecast_summary=row.forecast_summary, severe_warning=row.severe_warning,
        source=row.source, fetched_at=row.fetched_at,
        forecast_hourly=[HourlyForecast.model_validate(h) for h in row.forecast_hourly],
    )


def weather_history(db: Session, zone: str, limit: int = 2) -> list[WeatherReading]:
    """Newest first."""
    rows = db.scalars(select(m.WeatherSnapshot).where(m.WeatherSnapshot.zone == zone)
                      .order_by(m.WeatherSnapshot.fetched_at.desc(), m.WeatherSnapshot.id.desc()).limit(limit)).all()
    return [_reading(r) for r in rows]


def insert_weather(db: Session, r: WeatherReading) -> None:
    db.add(m.WeatherSnapshot(
        zone=r.zone, condition=r.condition, rainfall_mm_per_hr=r.rainfall_mm_per_hr, wind_kph=r.wind_kph,
        temperature_c=r.temperature_c, forecast_summary=r.forecast_summary,
        forecast_hourly=[h.model_dump() for h in r.forecast_hourly], severe_warning=r.severe_warning,
        source=r.source, fetched_at=r.fetched_at,
    ))


class DbWeatherSource:
    """Simulation-mode weather source: the latest snapshot stored in the database per zone.
    Simulation endpoints change the weather by inserting a new snapshot."""

    def __init__(self, session_factory):
        self.session_factory = session_factory

    def get(self, zone: str) -> WeatherReading | None:
        with self.session_factory() as db:
            history = weather_history(db, zone, limit=1)
        return history[0] if history else None


# ---------------------------------------------------------------- priorities & runs

def latest_priority(db: Session, incident_id: str) -> m.PriorityAssessmentRow | None:
    return db.scalars(select(m.PriorityAssessmentRow).where(m.PriorityAssessmentRow.incident_id == incident_id)
                      .order_by(m.PriorityAssessmentRow.assessed_at.desc(), m.PriorityAssessmentRow.id.desc())
                      .limit(1)).first()


def previous_run_started(db: Session, current_run_id: str) -> datetime | None:
    """Start time of the most recent completed run before this one (what counts as 'new since last run')."""
    return db.scalar(select(func.max(m.AgentRun.started_at))
                     .where(m.AgentRun.id != current_run_id, m.AgentRun.status == "completed"))
