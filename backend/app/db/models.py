"""SQLAlchemy ORM models: one class per database table.

Design notes (the "why"):
- Human-readable string IDs (INC-001, BOAT-001, ...) for entities a coordinator talks about;
  plain auto-increment integers for append-only logs (history, events, trace steps).
- Geometry columns use SRID 4326 (WGS84 lat/lng) so PostGIS distance functions work directly.
- Status columns carry CHECK constraints: the database itself rejects impossible values,
  even if a bug slips past the Python validation.
- Values the API exposes but that are *derived* are deliberately NOT stored here:
  shelter available capacity, an incident's current priority (= latest priority_assessments row),
  `duplicate_of` (from duplicate_links) and assigned resources (from active resource_assignments).
  Storing them twice would let them drift out of sync.
"""

from datetime import datetime, timezone
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


def utcnow() -> datetime:
    """Timezone-aware UTC 'now' (naive datetimes are ambiguous and banned in this project)."""
    return datetime.now(timezone.utc)


def check_in(column: str, values: tuple[str, ...], name: str) -> CheckConstraint:
    """CHECK constraint restricting a text column to an allowed set of values."""
    allowed = ", ".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{column} IN ({allowed})", name=name)


# Allowed values, kept in one place and mirrored by the Literal types in app/models/schemas.py.
PROVENANCES = ("known", "reported", "inferred")
INCIDENT_TYPES = (
    "flood", "cyclone", "earthquake", "wildfire", "landslide", "industrial_accident", "urban_emergency",
)
SOURCES = ("emergency_call", "field_team", "iot_sensor", "government_alert", "citizen_report")
PRIORITY_LEVELS = ("critical", "high", "moderate", "low", "verification_required")
RESOURCE_TYPES = ("rescue_boat", "rescue_team", "ambulance", "emergency_vehicle", "medical_team")
RESOURCE_STATUSES = ("available", "reserved", "deployed", "returning", "unavailable")
ASSIGNMENT_STATUSES = ("reserved", "deployed", "returning", "released")
ROAD_STATUSES = ("open", "partially_blocked", "closed", "unknown")
ACTION_STATUSES = ("pending", "approved", "rejected", "in_progress", "completed", "superseded")
ACTION_EVENTS = (
    "created", "approved", "rejected", "modified", "reassess_requested",
    "comment", "started", "completed", "superseded",
)
RUN_TRIGGERS = ("manual", "new_report", "road_change", "weather_change", "resource_change", "reassess")

TZDateTime = DateTime(timezone=True)


# ---------------------------------------------------------------- intake & incidents

class RawReport(Base):
    """An incoming report exactly as received, before normalization. Never modified afterwards."""

    __tablename__ = "raw_reports"
    __table_args__ = (
        check_in("source", SOURCES, "ck_raw_reports_source"),
        check_in("processing_status", ("received", "normalized", "failed"), "ck_raw_reports_status"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # RAW-001
    source: Mapped[str] = mapped_column(String(32))
    received_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    raw_text: Mapped[str | None] = mapped_column(Text)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    normalized_incident_id: Mapped[str | None] = mapped_column(ForeignKey("incidents.id"))
    processing_status: Mapped[str] = mapped_column(String(16), default="received")


class Incident(Base):
    """A normalized incident. `field_provenance` maps field name -> known/reported/inferred."""

    __tablename__ = "incidents"
    __table_args__ = (
        check_in("type", INCIDENT_TYPES, "ck_incidents_type"),
        check_in("source", SOURCES, "ck_incidents_source"),
        check_in("confidence", ("high", "medium", "low"), "ck_incidents_confidence"),
        check_in("verification_status", ("pending", "verified", "unverified"), "ck_incidents_verification"),
        check_in("status", ("active", "monitoring", "resolved"), "ck_incidents_status"),
        CheckConstraint("people_affected IS NULL OR people_affected >= 0", name="ck_incidents_people"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # INC-001
    type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    zone: Mapped[str] = mapped_column(String(64))
    # Nullable on purpose: an unknown location must stay unknown (-> "verification_required").
    geom: Mapped[Any | None] = mapped_column(Geometry("POINT", srid=4326))
    reported_at: Mapped[datetime] = mapped_column(TZDateTime)
    source: Mapped[str] = mapped_column(String(32))
    people_affected: Mapped[int | None] = mapped_column(Integer)  # NULL = not reported, never guessed
    assistance_requested: Mapped[list[str]] = mapped_column(JSONB, default=list)
    infrastructure_issues: Mapped[list[str]] = mapped_column(JSONB, default=list)
    # Input for the priority engine's "threat to life" factor (e.g. stranded, isolated).
    threat_indicators: Mapped[list[str]] = mapped_column(JSONB, default=list)
    confidence: Mapped[str] = mapped_column(String(8))
    verification_status: Mapped[str] = mapped_column(String(16), default="pending")
    status: Mapped[str] = mapped_column(String(16), default="active")
    supporting_info: Mapped[str | None] = mapped_column(Text)
    field_provenance: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class DuplicateLink(Base):
    """A suspected duplicate/related pair awaiting human review. Incidents are never deleted or merged."""

    __tablename__ = "duplicate_links"
    __table_args__ = (
        check_in("classification", ("potential_duplicate", "related"), "ck_dup_classification"),
        check_in("status", ("pending", "confirmed", "rejected"), "ck_dup_status"),
        CheckConstraint("incident_id <> candidate_id", name="ck_dup_not_self"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # DUP-001
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True)   # the newer report
    candidate_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True)  # the existing one
    score: Mapped[float] = mapped_column(Float)
    classification: Mapped[str] = mapped_column(String(32))
    breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    reviewed_by: Mapped[str | None] = mapped_column(String(64))
    reviewed_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class PriorityAssessmentRow(Base):
    """Every assessment is kept (history); the latest one per incident is the current priority."""

    __tablename__ = "priority_assessments"
    __table_args__ = (
        check_in("level", PRIORITY_LEVELS, "ck_priority_level"),
        CheckConstraint("score BETWEEN 0 AND 100", name="ck_priority_score"),
        Index("ix_priority_incident_time", "incident_id", "assessed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"))
    score: Mapped[int] = mapped_column(Integer)
    level: Mapped[str] = mapped_column(String(32))
    factors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    reasons: Mapped[list[str]] = mapped_column(JSONB, default=list)
    verification_needed: Mapped[list[str]] = mapped_column(JSONB, default=list)
    assessed_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


# ---------------------------------------------------------------- resources

class Resource(Base):
    __tablename__ = "resources"
    __table_args__ = (
        check_in("type", RESOURCE_TYPES, "ck_resources_type"),
        check_in("status", RESOURCE_STATUSES, "ck_resources_status"),
        CheckConstraint("capacity >= 0", name="ck_resources_capacity"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # BOAT-001
    type: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(100))
    base_name: Mapped[str] = mapped_column(String(100))
    geom: Mapped[Any] = mapped_column(Geometry("POINT", srid=4326))
    status: Mapped[str] = mapped_column(String(16), default="available")
    capacity: Mapped[int] = mapped_column(Integer)
    capabilities: Mapped[list[str]] = mapped_column(JSONB, default=list)
    expected_available_at: Mapped[datetime | None] = mapped_column(TZDateTime)


class ResourceAssignment(Base):
    """Links a resource to an incident. The partial unique index is the database-level guarantee
    against double allocation: at most ONE reserved/deployed assignment per resource, ever."""

    __tablename__ = "resource_assignments"
    __table_args__ = (
        check_in("status", ASSIGNMENT_STATUSES, "ck_assignment_status"),
        Index(
            "uq_active_assignment_per_resource",
            "resource_id",
            unique=True,
            postgresql_where=text("status IN ('reserved', 'deployed')"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    resource_id: Mapped[str] = mapped_column(ForeignKey("resources.id"), index=True)
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True)
    action_id: Mapped[str | None] = mapped_column(ForeignKey("actions.id"))
    status: Mapped[str] = mapped_column(String(16), default="reserved")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    released_at: Mapped[datetime | None] = mapped_column(TZDateTime)


class ResourceStatusHistory(Base):
    __tablename__ = "resource_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    resource_id: Mapped[str] = mapped_column(ForeignKey("resources.id"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(16))  # NULL for the initial registration
    to_status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


# ---------------------------------------------------------------- shelters, roads, weather

class Shelter(Base):
    """Available capacity is never stored: it is always capacity - current_occupancy."""

    __tablename__ = "shelters"
    __table_args__ = (
        check_in("status", ("open", "full", "closed"), "ck_shelters_status"),
        CheckConstraint("capacity >= 0 AND current_occupancy >= 0", name="ck_shelters_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # SHL-A
    name: Mapped[str] = mapped_column(String(100))
    geom: Mapped[Any] = mapped_column(Geometry("POINT", srid=4326))
    capacity: Mapped[int] = mapped_column(Integer)
    current_occupancy: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="open")
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class RoadSegment(Base):
    """Road registry. provenance distinguishes a KNOWN closure from an AI-INFERRED potential problem."""

    __tablename__ = "road_segments"
    __table_args__ = (
        check_in("status", ROAD_STATUSES, "ck_roads_status"),
        check_in("provenance", PROVENANCES, "ck_roads_provenance"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # RD-A
    name: Mapped[str] = mapped_column(String(100))
    serves_zone: Mapped[str] = mapped_column(String(64), index=True)
    geom: Mapped[Any] = mapped_column(Geometry("LINESTRING", srid=4326))
    status: Mapped[str] = mapped_column(String(32))
    provenance: Mapped[str] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class WeatherSnapshot(Base):
    """History of weather readings per zone; the latest per zone is current.
    Comparing with the previous one is how 'weather worsening' is detected."""

    __tablename__ = "weather_snapshots"
    __table_args__ = (
        check_in("source", ("open-meteo", "simulation"), "ck_weather_source"),
        Index("ix_weather_zone_time", "zone", "fetched_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone: Mapped[str] = mapped_column(String(64))
    condition: Mapped[str] = mapped_column(String(64))
    rainfall_mm_per_hr: Mapped[float] = mapped_column(Float)
    wind_kph: Mapped[float] = mapped_column(Float)
    temperature_c: Mapped[float] = mapped_column(Float)
    forecast_summary: Mapped[str] = mapped_column(Text)
    # [{"hour": 1, "precipitation_mm": 18.0, "wind_kph": 32.0}, ...] for the next 6 hours
    forecast_hourly: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    severe_warning: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(16))
    fetched_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


# ---------------------------------------------------------------- planning & actions

class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        check_in("trigger", RUN_TRIGGERS, "ck_runs_trigger"),
        check_in("status", ("running", "completed", "failed"), "ck_runs_status"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # RUN-001
    trigger: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    status: Mapped[str] = mapped_column(String(16), default="running")


class AgentStep(Base):
    """One agent's contribution to a run: the explainability trace shown in the UI."""

    __tablename__ = "agent_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    agent: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    input_summary: Mapped[str] = mapped_column(Text, default="")
    output_summary: Mapped[str] = mapped_column(Text, default="")
    # [{"name": ..., "args": {...}, "result_summary": ..., "ok": true}, ...]
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    llm_used: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)


class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = (
        check_in("variant", ("A", "B", "C"), "ck_plans_variant"),
        check_in("status", ("proposed", "superseded"), "ck_plans_status"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # PLAN-001
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id"), index=True)
    variant: Mapped[str] = mapped_column(String(1))
    title: Mapped[str] = mapped_column(String(200))
    strategy: Mapped[str] = mapped_column(Text)
    allocations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    shortages: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    explanation: Mapped[str] = mapped_column(Text, default="")
    assumptions: Mapped[list[str]] = mapped_column(JSONB, default=list)
    critic_findings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    plan_score: Mapped[float] = mapped_column(Float, default=0.0)
    is_recommended: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="proposed")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class Action(Base):
    """A recommended response action. Created as 'pending'; only a coordinator can approve it."""

    __tablename__ = "actions"
    __table_args__ = (
        check_in("status", ACTION_STATUSES, "ck_actions_status"),
        check_in("priority", PRIORITY_LEVELS, "ck_actions_priority"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # ACT-001
    incident_id: Mapped[str] = mapped_column(ForeignKey("incidents.id"), index=True)
    plan_id: Mapped[str | None] = mapped_column(ForeignKey("plans.id"))
    resource_ids: Mapped[list[str]] = mapped_column(JSONB, default=list)
    description: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    rationale: Mapped[list[str]] = mapped_column(JSONB, default=list)
    assumptions: Mapped[list[str]] = mapped_column(JSONB, default=list)
    # [{"label": "People affected", "value": "40", "provenance": "reported"}, ...]
    inputs_used: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    due_by: Mapped[datetime | None] = mapped_column(TZDateTime)


class ActionEvent(Base):
    """Append-only audit trail: who did what to an action, and when."""

    __tablename__ = "action_events"
    __table_args__ = (check_in("event", ACTION_EVENTS, "ck_action_events_event"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action_id: Mapped[str] = mapped_column(ForeignKey("actions.id"), index=True)
    event: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(64))
    comment: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


# ---------------------------------------------------------------- alerts & change log

class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        check_in(
            "kind",
            ("critical_incident", "severity_increase", "resource_unavailable", "route_blocked",
             "shelter_capacity", "weather_worsening", "action_overdue"),
            "ck_alerts_kind",
        ),
        check_in("severity", ("critical", "warning", "info"), "ck_alerts_severity"),
    )

    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # ALT-001
    kind: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)
    related_id: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)


class ConditionChange(Base):
    """Log of changed conditions; each one is a potential replanning trigger."""

    __tablename__ = "condition_changes"
    __table_args__ = (check_in("provenance", PROVENANCES, "ck_changes_provenance"),)

    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # CHG-001
    entity: Mapped[str] = mapped_column(String(200))       # display name, e.g. "Main Road to Zone C"
    entity_id: Mapped[str | None] = mapped_column(String(16))  # machine ref, e.g. "RD-C" (for replanning)
    field: Mapped[str] = mapped_column(String(64))
    old_value: Mapped[str] = mapped_column(Text)
    new_value: Mapped[str] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    provenance: Mapped[str] = mapped_column(String(16))


class SituationReport(Base):
    """A generated situation report. The PDF lives on disk (REPORTS_DIR); this row is its index."""

    __tablename__ = "situation_reports"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # SR-001
    title: Mapped[str] = mapped_column(String(200))
    generated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("agent_runs.id"))
    narrative: Mapped[str] = mapped_column(Text)
    narrative_llm: Mapped[bool] = mapped_column(Boolean, default=False)
    file_name: Mapped[str] = mapped_column(String(100))
