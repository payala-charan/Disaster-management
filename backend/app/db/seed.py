"""Load the base scenario from backend/data/seed/ into the database.

Usage:  python -m app.db.seed --reset

Three steps, kept separate so the first two can be unit-tested without a database:
1. load_seed()          read + validate every JSON file with Pydantic (typos fail loudly)
2. check_consistency()  cross-file rules (references exist, deployed resources have an action, ...)
3. write_seed()         insert rows, resolving relative times against one shared "now"

Raw incidents get NO priority here. Priorities are computed by the engine on the first agent run.
Duplicate links are not precomputed either: write_seed runs the same duplicate engine the Intake agent
uses, over the seed incidents in report order, and creates pending links for human review.
"""

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import BACKEND_DIR
from app.db import models as m
from app.db.geo import path_to_geom, point_to_geom
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.models.seed_data import (
    Gazetteer,
    Scenario,
    ScenarioSetup,
    SeedAction,
    SeedChange,
    SeedIncident,
    SeedResource,
    SeedRoad,
    SeedShelter,
    SeedWeather,
)

SEED_DIR = BACKEND_DIR / "data" / "seed"
SCENARIO_DIR = BACKEND_DIR / "data" / "scenarios"

# Which assignment/resource status an action status implies. Pending actions reserve nothing:
# resources are only reserved when a coordinator approves.
ACTIVE_ACTION_TO_RESOURCE_STATUS = {"approved": "reserved", "in_progress": "deployed"}


@dataclass
class SeedBundle:
    gazetteer: Gazetteer
    incidents: list[SeedIncident]
    resources: list[SeedResource]
    shelters: list[SeedShelter]
    roads: list[SeedRoad]
    weather: list[SeedWeather]
    actions: list[SeedAction]
    changes: list[SeedChange]


def _read[T](path: Path, model: type[T]) -> T:
    return TypeAdapter(model).validate_python(json.loads(path.read_text()))


def load_seed(seed_dir: Path = SEED_DIR) -> SeedBundle:
    return SeedBundle(
        gazetteer=_read(seed_dir / "zones.json", Gazetteer),
        incidents=_read(seed_dir / "incidents.json", list[SeedIncident]),
        resources=_read(seed_dir / "resources.json", list[SeedResource]),
        shelters=_read(seed_dir / "shelters.json", list[SeedShelter]),
        roads=_read(seed_dir / "roads.json", list[SeedRoad]),
        weather=_read(seed_dir / "weather.json", list[SeedWeather]),
        actions=_read(seed_dir / "actions.json", list[SeedAction]),
        changes=_read(seed_dir / "changes.json", list[SeedChange]),
    )


def load_scenario(name: str, scenario_dir: Path = SCENARIO_DIR) -> Scenario:
    return _read(scenario_dir / f"{name}.json", Scenario)


def list_scenarios(scenario_dir: Path = SCENARIO_DIR) -> list[str]:
    return sorted(p.stem for p in scenario_dir.glob("*.json"))


def check_consistency(b: SeedBundle) -> list[str]:
    """Return human-readable problems (empty list = consistent)."""
    errors: list[str] = []

    def unique(kind: str, ids: list[str]) -> None:
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            errors.append(f"duplicate {kind} ids: {dupes}")

    zones = {z.name for z in b.gazetteer.zones}
    bases = {base.name for base in b.gazetteer.bases}
    incident_ids = {i.id for i in b.incidents}
    resources = {r.id: r for r in b.resources}

    unique("incident", [i.id for i in b.incidents])
    unique("resource", [r.id for r in b.resources])
    unique("shelter", [s.id for s in b.shelters])
    unique("road", [r.id for r in b.roads])
    unique("action", [a.id for a in b.actions])
    unique("change", [c.id for c in b.changes])

    for i in b.incidents:
        if i.zone not in zones:
            errors.append(f"{i.id}: zone {i.zone!r} not in gazetteer")
    for r in b.roads:
        if r.serves_zone not in zones:
            errors.append(f"{r.id}: serves_zone {r.serves_zone!r} not in gazetteer")
    for w in b.weather:
        if w.zone not in zones:
            errors.append(f"weather: zone {w.zone!r} not in gazetteer")
    for r in b.resources:
        if r.base_name not in bases:
            errors.append(f"{r.id}: base {r.base_name!r} not in gazetteer")

    # Every reserved/deployed resource must be explained by exactly one active action, and vice versa.
    claimed: dict[str, str] = {}
    for a in b.actions:
        if a.incident_id not in incident_ids:
            errors.append(f"{a.id}: unknown incident {a.incident_id}")
        expected = ACTIVE_ACTION_TO_RESOURCE_STATUS.get(a.status)
        for rid in a.resource_ids:
            if rid not in resources:
                errors.append(f"{a.id}: unknown resource {rid}")
                continue
            if expected is None:
                continue
            if rid in claimed:
                errors.append(f"{rid} double-allocated to {claimed[rid]} and {a.id}")
            claimed[rid] = a.id
            if resources[rid].status != expected:
                errors.append(f"{a.id} is {a.status} but {rid} is {resources[rid].status} (expected {expected})")
    for r in b.resources:
        if r.status in ("reserved", "deployed") and r.id not in claimed:
            errors.append(f"{r.id} is {r.status} but no approved/in-progress action uses it")

    return errors


def write_seed(db: Session, b: SeedBundle, now: datetime | None = None) -> None:
    """Insert the bundle. All relative times resolve against the same `now` so they stay coherent."""
    now = now or datetime.now(timezone.utc)

    for i in b.incidents:
        db.add(m.Incident(
            id=i.id, type=i.type, title=i.title, description=i.description, zone=i.zone,
            geom=point_to_geom(i.location) if i.location else None,
            reported_at=i.reported_at.resolve(now), source=i.source,
            people_affected=i.people_affected, assistance_requested=i.assistance_requested,
            infrastructure_issues=i.infrastructure_issues, threat_indicators=i.threat_indicators,
            confidence=i.confidence, verification_status=i.verification_status, status=i.status,
            supporting_info=i.supporting_info, field_provenance=i.field_provenance,
        ))
    for r in b.resources:
        db.add(m.Resource(
            id=r.id, type=r.type, name=r.name, base_name=r.base_name, geom=point_to_geom(r.location),
            status=r.status, capacity=r.capacity, capabilities=r.capabilities,
            expected_available_at=r.expected_available_at.resolve(now) if r.expected_available_at else None,
        ))
        db.add(m.ResourceStatusHistory(
            resource_id=r.id, from_status=None, to_status=r.status, reason="Initial registry (seed)", changed_at=now,
        ))
    for s in b.shelters:
        db.add(m.Shelter(
            id=s.id, name=s.name, geom=point_to_geom(s.location), capacity=s.capacity,
            current_occupancy=s.current_occupancy, status=s.status, updated_at=now,
        ))
    for rd in b.roads:
        db.add(m.RoadSegment(
            id=rd.id, name=rd.name, serves_zone=rd.serves_zone, geom=path_to_geom(rd.path),
            status=rd.status, provenance=rd.provenance, note=rd.note, updated_at=rd.updated_at.resolve(now),
        ))
    for w in b.weather:
        db.add(m.WeatherSnapshot(
            zone=w.zone, condition=w.condition, rainfall_mm_per_hr=w.rainfall_mm_per_hr, wind_kph=w.wind_kph,
            temperature_c=w.temperature_c, forecast_summary=w.forecast_summary,
            forecast_hourly=[h.model_dump() for h in w.forecast_hourly],
            severe_warning=w.severe_warning, source=w.source, fetched_at=w.fetched_at.resolve(now),
        ))
    db.flush()  # incidents/resources must exist before actions and assignments reference them

    def at(t):  # optional relative time -> datetime | None
        return t.resolve(now) if t else None

    for a in b.actions:
        db.add(m.Action(
            id=a.id, incident_id=a.incident_id, plan_id=None, resource_ids=a.resource_ids,
            description=a.description, priority=a.priority, status=a.status, rationale=a.rationale,
            assumptions=a.assumptions, inputs_used=a.inputs_used, created_at=a.created_at.resolve(now),
            approved_at=at(a.approved_at), started_at=at(a.started_at),
            completed_at=at(a.completed_at), due_by=at(a.due_by),
        ))
        db.flush()
        for e in a.events:
            db.add(m.ActionEvent(action_id=a.id, event=e.event, actor=e.actor, comment=e.comment, at=e.at.resolve(now)))
        assignment_status = ACTIVE_ACTION_TO_RESOURCE_STATUS.get(a.status)
        if assignment_status:
            for rid in a.resource_ids:
                db.add(m.ResourceAssignment(
                    resource_id=rid, incident_id=a.incident_id, action_id=a.id,
                    status=assignment_status, created_at=at(a.approved_at) or now,
                ))
    for c in b.changes:
        db.add(m.ConditionChange(
            id=c.id, entity=c.entity, entity_id=c.entity_id, field=c.field, old_value=c.old_value,
            new_value=c.new_value, changed_at=c.changed_at.resolve(now), provenance=c.provenance,
        ))
    db.flush()
    link_duplicates(db)
    db.commit()


def write_setup(db: Session, setup: ScenarioSetup, now: datetime | None = None) -> None:
    """Add a scenario's extra registry entries (roads, shelters, resources) on top of the seed."""
    now = now or datetime.now(timezone.utc)
    for rd in setup.roads:
        db.add(m.RoadSegment(id=rd.id, name=rd.name, serves_zone=rd.serves_zone, geom=path_to_geom(rd.path),
                             status=rd.status, provenance=rd.provenance, note=rd.note, updated_at=rd.updated_at.resolve(now)))
    for s in setup.shelters:
        db.add(m.Shelter(id=s.id, name=s.name, geom=point_to_geom(s.location), capacity=s.capacity,
                         current_occupancy=s.current_occupancy, status=s.status, updated_at=now))
    for r in setup.resources:
        db.add(m.Resource(id=r.id, type=r.type, name=r.name, base_name=r.base_name, geom=point_to_geom(r.location),
                          status=r.status, capacity=r.capacity, capabilities=r.capabilities))
        db.add(m.ResourceStatusHistory(resource_id=r.id, from_status=None, to_status=r.status,
                                       reason="Added by scenario setup", changed_at=now))
    db.flush()


def link_duplicates(db: Session) -> int:
    """Compare each incident with the ones reported before it (as Intake would have) -> pending links."""
    from app.core.config import get_settings
    from app.db.repository import incident_facts, next_id
    from app.engines.duplicates import find_candidates

    settings = get_settings()
    facts = sorted(incident_facts(db).values(), key=lambda f: (f.reported_at, f.id))
    created = 0
    for n, newer in enumerate(facts):
        for cand in find_candidates(newer, facts[:n], settings.duplicate_distance_m, settings.duplicate_time_window_min):
            db.add(m.DuplicateLink(id=next_id(db, m.DuplicateLink, "DUP"), incident_id=cand.incident_id,
                                   candidate_id=cand.candidate_id, score=cand.score, classification=cand.classification,
                                   breakdown=cand.breakdown.model_dump(), status="pending"))
            created += 1
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="drop and recreate all tables first (destroys data)")
    args = parser.parse_args()

    bundle = load_seed()
    problems = check_consistency(bundle)
    if problems:
        raise SystemExit("Seed data is inconsistent:\n  - " + "\n  - ".join(problems))

    init_db(drop=args.reset)
    with SessionLocal() as db:
        if db.scalar(select(func.count()).select_from(m.Incident)):
            raise SystemExit("Database already has data. Use --reset to wipe and reload it.")
        write_seed(db, bundle)
        counts = {
            table: db.scalar(select(func.count()).select_from(model))
            for table, model in [
                ("incidents", m.Incident), ("resources", m.Resource), ("shelters", m.Shelter),
                ("road_segments", m.RoadSegment), ("weather_snapshots", m.WeatherSnapshot),
                ("actions", m.Action), ("resource_assignments", m.ResourceAssignment),
                ("condition_changes", m.ConditionChange), ("duplicate_links", m.DuplicateLink),
            ]
        }
    print("Seeded: " + ", ".join(f"{k}={v}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
