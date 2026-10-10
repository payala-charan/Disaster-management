"""Health, situation snapshot, roads/access, shelters and weather."""

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.agents.geospatial import build_access_analysis
from app.api.deps import get_actor, get_db, get_services, replan_if_needed
from app.db import models as m
from app.db import repository as repo
from app.engines.access import assess_zone_access
from app.models import schemas as s
from app.services import operations as ops
from app.services import serializers as ser
from app.services.context import Services
from app.services.errors import get_or_404

router = APIRouter()


@router.get("/health")
def health(svc: Services = Depends(get_services), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        postgis = db.execute(text("SELECT PostGIS_Version()")).scalar()
    except Exception as exc:  # noqa: BLE001
        postgis = f"unavailable ({type(exc).__name__})"
    st = svc.settings
    return {"status": "ok", "postgis": postgis, "llm": svc.deps.llm.provider if svc.deps.llm.enabled else "fallback only",
            "llm_model": svc.deps.llm.model if svc.deps.llm.enabled else None,
            "weather_mode": st.weather_mode, "routing_mode": st.routing_mode}


@router.get("/zones", response_model=list[s.ZoneInfo])
def zones():
    return [s.ZoneInfo(name=z.name, center=z.center, description=z.description) for z in repo.gazetteer().zones]


@router.get("/situation", response_model=s.SituationSnapshot)
def situation(db: Session = Depends(get_db)):
    return ser.situation(db)


# ---------------------------------------------------------------- roads & access

@router.get("/roads", response_model=list[s.RoadSegment])
def roads(db: Session = Depends(get_db)):
    return ser.roads(db)


@router.patch("/roads/{road_id}", response_model=s.RoadSegment)
def patch_road(road_id: str, body: s.RoadPatch, background: BackgroundTasks, svc: Services = Depends(get_services),
               db: Session = Depends(get_db), actor: str = Depends(get_actor)):
    impact = ops.update_road(svc, db, road_id, body, actor)
    replan_if_needed(svc, impact, "road_change", background)
    return next(r for r in ser.roads(db) if r.id == road_id)


@router.get("/access/{incident_id}", response_model=s.AccessAnalysis)
def access(incident_id: str, svc: Services = Depends(get_services), db: Session = Depends(get_db)):
    get_or_404(db, m.Incident, incident_id)
    inc = repo.incident_facts(db, include_resolved=True)[incident_id]
    roads = repo.road_facts(db)
    available = [r for r in repo.resource_facts(db) if r.status == "available"]
    travel = svc.deps.routing.table({r.id: r.location for r in available}, {inc.id: inc.location}) if inc.location else {}
    return build_access_analysis(inc, assess_zone_access(inc.zone, roads), roads, travel,
                                 {r.id: r.base_name for r in available}, svc.now())


# ---------------------------------------------------------------- shelters

@router.get("/shelters", response_model=list[s.Shelter])
def shelters(db: Session = Depends(get_db)):
    return ser.shelters(db)


@router.patch("/shelters/{shelter_id}", response_model=s.Shelter)
def patch_shelter(shelter_id: str, body: s.ShelterPatch, background: BackgroundTasks,
                  svc: Services = Depends(get_services), db: Session = Depends(get_db), actor: str = Depends(get_actor)):
    impact = ops.update_shelter(svc, db, shelter_id, body, actor)
    replan_if_needed(svc, impact, "resource_change", background)
    return next(x for x in ser.shelters(db) if x.id == shelter_id)


# ---------------------------------------------------------------- weather

@router.get("/weather", response_model=list[s.WeatherSnapshot])
def weather(db: Session = Depends(get_db)):
    return ser.weather(db)


@router.post("/weather/refresh", response_model=list[s.WeatherSnapshot])
def refresh_weather(background: BackgroundTasks, svc: Services = Depends(get_services), db: Session = Depends(get_db)):
    snapshots = ops.refresh_weather(svc, db)
    svc.schedule_run("weather_change", background)  # the weather agent decides whether it worsened
    return snapshots
