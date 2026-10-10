"""Database-level guarantees: seed round trip, geometry handling, constraints and the
partial unique index that makes double allocation impossible even if application code has a bug."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db import models as m
from app.db.geo import geom_to_path, geom_to_point

pytestmark = pytest.mark.db


def test_seed_round_trip(db):
    assert db.scalar(select(func.count()).select_from(m.Incident)) == 4
    assert db.scalar(select(func.count()).select_from(m.Resource)) == 23

    inc3 = db.get(m.Incident, "INC-003")
    assert geom_to_point(inc3.geom).model_dump() == {"lat": 12.979, "lng": 80.181}  # lat/lng not swapped
    assert inc3.field_provenance["infrastructure_issues"] == "known"
    assert inc3.reported_at.tzinfo is not None  # timezone-aware

    assert db.get(m.Incident, "INC-004").people_affected is None
    assert len(geom_to_path(db.get(m.RoadSegment, "RD-C").geom)) == 3


def test_seed_creates_no_priorities_or_alerts(db):
    assert db.scalar(select(func.count()).select_from(m.PriorityAssessmentRow)) == 0
    assert db.scalar(select(func.count()).select_from(m.Alert)) == 0


def test_deployed_resources_have_active_assignments(db):
    rows = db.scalars(select(m.ResourceAssignment).where(m.ResourceAssignment.status == "deployed")).all()
    assert sorted(r.resource_id for r in rows) == ["BOAT-001", "TEAM-001"]
    assert {r.action_id for r in rows} == {"ACT-001"}


def test_partial_unique_index_blocks_double_allocation(db):
    # BOAT-001 already has a 'deployed' assignment to INC-001 (from the seed).
    db.add(m.ResourceAssignment(resource_id="BOAT-001", incident_id="INC-003", status="reserved"))
    with pytest.raises(IntegrityError, match="uq_active_assignment_per_resource"):
        db.flush()


def test_released_assignment_frees_the_resource(db):
    old = db.scalars(select(m.ResourceAssignment).where(m.ResourceAssignment.resource_id == "BOAT-001")).one()
    old.status = "released"
    db.flush()
    db.add(m.ResourceAssignment(resource_id="BOAT-001", incident_id="INC-003", status="reserved"))
    db.flush()  # allowed: the index only covers reserved/deployed rows


def test_check_constraint_rejects_invalid_status(db):
    db.get(m.Resource, "BOAT-002").status = "teleported"
    with pytest.raises(IntegrityError, match="ck_resources_status"):
        db.flush()


def test_postgis_nearest_shelter(db):
    """ST_DistanceSphere gives metres on the WGS84 sphere; the Geospatial agent will use this query."""
    inc3 = select(m.Incident.geom).where(m.Incident.id == "INC-003").scalar_subquery()
    distance = func.ST_DistanceSphere(m.Shelter.geom, inc3)
    nearest_id, metres = db.execute(select(m.Shelter.id, distance).order_by(distance).limit(1)).one()
    assert nearest_id == "SHL-C"
    assert 3_000 < metres < 4_000
