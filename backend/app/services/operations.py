"""Changes to the operational picture: roads, resources, shelters, weather, incidents, duplicates, intake.

Each function validates, applies the change in ONE transaction, raises the documented alerts, goes
through replanning.handle_change for anything that can affect a plan, commits, then broadcasts.
It returns the impact so the API can start a new agent run when needed.

Alert rules:
- route_blocked: a road becomes closed (critical) or partially blocked (warning) and that is a worsening
- resource_unavailable: a resource becomes unavailable (critical if it was assigned, else warning)
- shelter_capacity: utilization crosses SHELTER_ALERT_THRESHOLD (warning); shelter becomes full or closed (critical)
- weather_worsening / critical_incident / severity_increase: raised by the agents during the run
Manual resource changes may only set 'available' or 'unavailable'. Reserving, deploying and returning
happen through the action workflow, so the audit trail stays complete.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as m
from app.db.geo import point_to_geom
from app.db.repository import ACTIVE_ASSIGNMENT, gazetteer, incident_facts, insert_weather, next_id, weather_history
from app.engines.duplicates import find_candidates
from app.engines.replanning import ReplanImpact, road_worsened, weather_worsened
from app.engines.resources import InvalidTransition, validate_transition
from app.engines.shelters import crossed_threshold
from app.integrations import fallback_extract as fx
from app.integrations.weather import WeatherReading
from app.models import schemas as s
from app.models.seed_data import HourlyForecast
from app.services.context import Services
from app.services.errors import Conflict, Invalid, get_or_404
from app.services.replanning import handle_change
from app.services.serializers import weather as weather_snapshots

MANUAL_RESOURCE_TARGETS = {"available", "unavailable"}


def _finish(svc: Services, db: Session, alerts: list[m.Alert]) -> None:
    db.commit()
    svc.broadcast_alerts(alerts)
    svc.bus.publish("situation_changed", {})


# ---------------------------------------------------------------- roads

def update_road(svc: Services, db: Session, road_id: str, patch: s.RoadPatch, actor: str) -> ReplanImpact | None:
    road = get_or_404(db, m.RoadSegment, road_id, for_update=True)
    old_status, old_prov = road.status, road.provenance
    if (old_status, old_prov, road.note) == (patch.status, patch.provenance, patch.note):
        return None
    road.status, road.provenance, road.updated_at = patch.status, patch.provenance, svc.now()
    road.note = patch.note if patch.note is not None else road.note
    impact = handle_change(svc, db, kind="road", entity_id=road.id, entity_name=road.name, field="status",
                           old=old_status, new=patch.status, provenance=patch.provenance, zone=road.serves_zone)
    alerts = []
    if impact.worsened and patch.status in ("closed", "partially_blocked"):
        severity = "critical" if patch.status == "closed" else "warning"
        alerts.append(svc.add_alert(db, "route_blocked", severity,
                                    f"{road.name} ({road.id}) changed to {patch.status.replace('_', ' ').upper()} "
                                    f"({patch.provenance}, reported by {actor})", road.id))
    _finish(svc, db, alerts)
    return impact


# ---------------------------------------------------------------- resources

def change_resource_status(svc: Services, db: Session, resource_id: str, patch: s.ResourceStatusPatch,
                           actor: str) -> ReplanImpact:
    res = get_or_404(db, m.Resource, resource_id, for_update=True)
    if patch.status not in MANUAL_RESOURCE_TARGETS:
        raise Conflict(f"'{patch.status}' is set through the action workflow (approve/start/complete), not manually")
    try:
        validate_transition(res.status, patch.status)
    except InvalidTransition as exc:
        raise Conflict(str(exc)) from exc

    now, old = svc.now(), res.status
    assignments = db.scalars(select(m.ResourceAssignment).where(
        m.ResourceAssignment.resource_id == res.id,
        m.ResourceAssignment.status.in_([*ACTIVE_ASSIGNMENT, "returning"]))).all()
    was_assigned = any(a.status in ACTIVE_ASSIGNMENT for a in assignments)
    for a in assignments:  # unavailable: the assignment ends; returning -> available: back at base
        a.status, a.released_at = "released", now

    res.status = patch.status
    res.expected_available_at = patch.expected_available_at if patch.status == "unavailable" else None
    db.add(m.ResourceStatusHistory(resource_id=res.id, from_status=old, to_status=patch.status,
                                   reason=f"{patch.reason} ({actor})", changed_at=now))
    impact = handle_change(svc, db, kind="resource", entity_id=res.id, entity_name=res.name, field="status",
                           old=old, new=patch.status, provenance="known")
    alerts = []
    if patch.status == "unavailable":
        alerts.append(svc.add_alert(db, "resource_unavailable", "critical" if was_assigned else "warning",
                                    f"{res.id} ({res.name}) is unavailable: {patch.reason}"
                                    + (" (it was assigned: affected actions need review)" if was_assigned else ""), res.id))
    _finish(svc, db, alerts)
    return impact


def resource_history(db: Session, resource_id: str) -> list[s.ResourceStatusChange]:
    get_or_404(db, m.Resource, resource_id)
    rows = db.scalars(select(m.ResourceStatusHistory).where(m.ResourceStatusHistory.resource_id == resource_id)
                      .order_by(m.ResourceStatusHistory.changed_at.desc(), m.ResourceStatusHistory.id.desc()))
    return [s.ResourceStatusChange(resource_id=r.resource_id, from_status=r.from_status, to_status=r.to_status,
                                   reason=r.reason, changed_at=r.changed_at) for r in rows]


# ---------------------------------------------------------------- shelters

def update_shelter(svc: Services, db: Session, shelter_id: str, patch: s.ShelterPatch, actor: str) -> ReplanImpact | None:
    sh = get_or_404(db, m.Shelter, shelter_id, for_update=True)
    old_occ, old_status = sh.current_occupancy, sh.status
    if patch.current_occupancy is not None:
        if patch.current_occupancy < 0:
            raise Invalid("current_occupancy cannot be negative")
        sh.current_occupancy = patch.current_occupancy
    if patch.status is not None:
        sh.status = patch.status
    elif sh.status == "open" and sh.current_occupancy >= sh.capacity:
        sh.status = "full"   # documented automatic rule
    elif sh.status == "full" and sh.current_occupancy < sh.capacity:
        sh.status = "open"
    sh.updated_at = svc.now()

    alerts, impact = [], None
    threshold = svc.settings.shelter_alert_threshold
    if sh.current_occupancy != old_occ:
        db.add(m.ConditionChange(id=next_id(db, m.ConditionChange, "CHG"), entity=sh.name, entity_id=sh.id,
                                 field="current_occupancy", old_value=str(old_occ), new_value=str(sh.current_occupancy),
                                 changed_at=svc.now(), provenance="reported"))
        if crossed_threshold(old_occ, sh.current_occupancy, sh.capacity, threshold):
            alerts.append(svc.add_alert(db, "shelter_capacity", "warning",
                                        f"{sh.name} ({sh.id}) at {sh.current_occupancy / sh.capacity:.0%} "
                                        f"({sh.current_occupancy}/{sh.capacity}); alert threshold {threshold:.0%}", sh.id))
    if sh.status != old_status:
        impact = handle_change(svc, db, kind="shelter", entity_id=sh.id, entity_name=sh.name, field="status",
                               old=old_status, new=sh.status, provenance="reported")
        if sh.status in ("full", "closed"):
            alerts.append(svc.add_alert(db, "shelter_capacity", "critical",
                                        f"{sh.name} ({sh.id}) is {sh.status.upper()}: direct evacuees elsewhere", sh.id))
    _finish(svc, db, alerts)
    return impact


# ---------------------------------------------------------------- weather

def inject_weather(svc: Services, db: Session, body: s.WeatherInject) -> ReplanImpact:
    if svc.settings.weather_mode != "simulation":
        raise Conflict("Weather injection needs WEATHER_MODE=simulation (live mode always uses Open-Meteo)")
    previous = weather_history(db, body.zone, limit=1)
    reading = WeatherReading(**body.model_dump(exclude={"forecast_hourly"}), source="simulation", fetched_at=svc.now(),
                             forecast_hourly=[HourlyForecast.model_validate(h) for h in body.forecast_hourly])
    insert_weather(db, reading)
    worse = weather_worsened(previous[0].facts() if previous else None, reading.facts())
    impact = handle_change(svc, db, kind="weather", entity_id=body.zone, entity_name=f"Weather {body.zone}",
                           field="conditions", old=f"{previous[0].condition}, {previous[0].rainfall_mm_per_hr:g} mm/h" if previous else None,
                           new=f"{reading.condition}, {reading.rainfall_mm_per_hr:g} mm/h", provenance="known",
                           zone=body.zone, worsened=worse)
    _finish(svc, db, [])
    return impact


def refresh_weather(svc: Services, db: Session) -> list[s.WeatherSnapshot]:
    """Fetch every zone with an active incident now (live mode stores the new readings)."""
    centers = {z.name: z.center for z in gazetteer().zones}
    zones = {i.zone for i in db.scalars(select(m.Incident).where(m.Incident.status != "resolved"))}
    for zone in sorted(zones & centers.keys()):
        reading = svc.deps.weather.get(zone, centers[zone], force_refresh=True)
        latest = weather_history(db, zone, limit=1)
        if reading and reading.source == "open-meteo" and (not latest or reading.fetched_at > latest[0].fetched_at):
            insert_weather(db, reading)
    db.commit()
    return weather_snapshots(db)


# ---------------------------------------------------------------- incidents, duplicates, intake

def link_duplicates(svc: Services, db: Session, incident_id: str) -> list[str]:
    facts = incident_facts(db)
    found = find_candidates(facts[incident_id], [f for i, f in facts.items() if i != incident_id],
                            svc.settings.duplicate_distance_m, svc.settings.duplicate_time_window_min)
    ids = []
    for c in found:
        link = m.DuplicateLink(id=next_id(db, m.DuplicateLink, "DUP"), incident_id=c.incident_id,
                               candidate_id=c.candidate_id, score=c.score, classification=c.classification,
                               breakdown=c.breakdown.model_dump(), status="pending", created_at=svc.now())
        db.add(link)
        ids.append(link.id)
    return ids


def create_incident(svc: Services, db: Session, body: s.IncidentCreate, actor: str) -> str:
    """Manual registration by an authorized user. Threat labels come from the LLM classifier (with
    fallback); infrastructure labels from the deterministic extractor. The rest is as submitted."""
    text = f"{body.title}. {body.description}"
    threats = svc.deps.llm.classify_threat(text)
    incident = m.Incident(
        id=next_id(db, m.Incident, "INC"), type=body.type, title=body.title, description=body.description,
        zone=body.zone, geom=point_to_geom(body.location), reported_at=body.reported_at, source=body.source,
        people_affected=body.people_affected, assistance_requested=body.assistance_requested,
        infrastructure_issues=fx.extract_infrastructure(text), threat_indicators=threats.threat_indicators,
        confidence="medium", verification_status="pending", status="active",
        supporting_info=body.supporting_info, created_at=svc.now(),
        field_provenance={"location": "reported", "assistance_requested": "reported", "threat_indicators": "inferred",
                          "infrastructure_issues": "inferred",
                          **({"people_affected": "reported"} if body.people_affected is not None else {})},
    )
    db.add(incident)
    db.add(m.RawReport(id=next_id(db, m.RawReport, "RAW"), source=body.source, received_at=svc.now(), raw_text=text,
                       raw_payload={"registered_by": actor, **body.model_dump(mode="json")},
                       normalized_incident_id=incident.id, processing_status="normalized"))
    db.flush()
    link_duplicates(svc, db, incident.id)
    _finish(svc, db, [])
    return incident.id


def patch_incident(svc: Services, db: Session, incident_id: str, patch: s.IncidentPatch, actor: str) -> ReplanImpact | None:
    inc = get_or_404(db, m.Incident, incident_id, for_update=True)
    changes: list[tuple[str, object, object, str]] = []
    if patch.verification_status and patch.verification_status != inc.verification_status:
        changes.append(("verification_status", inc.verification_status, patch.verification_status, "known"))
        inc.verification_status = patch.verification_status
    if patch.status and patch.status != inc.status:
        changes.append(("status", inc.status, patch.status, "known"))
        inc.status = patch.status
    new_people = None if patch.clear_people_affected else patch.people_affected
    if (patch.clear_people_affected or patch.people_affected is not None) and new_people != inc.people_affected:
        if new_people is not None and new_people < 0:
            raise Invalid("people_affected cannot be negative")
        changes.append(("people_affected", inc.people_affected, new_people, "reported"))
        inc.people_affected = new_people
        prov = dict(inc.field_provenance)
        if new_people is None:
            prov.pop("people_affected", None)
        else:
            prov["people_affected"] = "reported"
        inc.field_provenance = prov
    if not changes:
        return None
    impact = None
    for field, old, new, prov in changes:
        impact = handle_change(svc, db, kind="incident", entity_id=inc.id, entity_name=inc.id,
                               field=field, old=str(old), new=str(new), provenance=prov)
    _finish(svc, db, [])
    return impact


def review_duplicate(svc: Services, db: Session, link_id: str, decision: str, actor: str) -> m.DuplicateLink:
    link = get_or_404(db, m.DuplicateLink, link_id, for_update=True)
    if link.status != "pending":
        raise Conflict(f"{link_id} was already {link.status}")
    link.status, link.reviewed_by, link.reviewed_at = decision, actor, svc.now()
    db.add(m.ConditionChange(id=next_id(db, m.ConditionChange, "CHG"), entity=link.incident_id, entity_id=link.incident_id,
                             field=f"duplicate_of {link.candidate_id}", old_value="pending", new_value=decision,
                             changed_at=svc.now(), provenance="known"))
    _finish(svc, db, [])
    return link


def submit_report(svc: Services, db: Session, body: s.IntakeReportIn) -> str:
    if not (body.text or body.payload):
        raise Invalid("A report needs text and/or payload")
    raw = m.RawReport(id=next_id(db, m.RawReport, "RAW"), source=body.source, raw_text=body.text,
                      raw_payload=body.payload, received_at=body.received_at or svc.now(), processing_status="received")
    db.add(raw)
    db.commit()
    return raw.id

