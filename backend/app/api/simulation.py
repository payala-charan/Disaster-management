"""Simulation endpoints that drive the live demo. Each one goes through the same services as the real API."""

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_actor, get_db, get_services, replan_if_needed
from app.models import schemas as s
from app.models.seed_data import IncidentUpdateStep
from app.services import operations as ops
from app.services import simulation as sim
from app.services.context import Services

router = APIRouter(prefix="/simulation", tags=["simulation"])


class RoadSim(s.RoadPatch):
    road_id: str


class ShelterSim(s.ShelterPatch):
    shelter_id: str


class ResourceSim(s.ResourceStatusPatch):
    resource_id: str


class IncidentSim(s.Contract):
    incident_id: str
    changes: dict[str, Any]
    provenance: s.Provenance = "reported"


@router.post("/reset")
def reset(svc: Services = Depends(get_services)) -> dict[str, str]:
    sim.reset(svc)
    return {"status": "reset", "message": "Database reloaded from the seed scenario"}


@router.post("/report", status_code=202)
def report(body: s.IntakeReportIn, background: BackgroundTasks, svc: Services = Depends(get_services),
           db: Session = Depends(get_db)) -> dict[str, str]:
    raw_id = ops.submit_report(svc, db, body)
    svc.schedule_run("new_report", background)
    return {"raw_report_id": raw_id}


@router.post("/road")
def road(body: RoadSim, background: BackgroundTasks, svc: Services = Depends(get_services),
         db: Session = Depends(get_db), actor: str = Depends(get_actor)) -> dict[str, Any]:
    impact = ops.update_road(svc, db, body.road_id, s.RoadPatch(**body.model_dump(exclude={"road_id"})), actor)
    return {"replanning": replan_if_needed(svc, impact, "road_change", background),
            "impact": impact.model_dump(mode="json") if impact else None}


@router.post("/weather")
def weather(body: s.WeatherInject, background: BackgroundTasks, svc: Services = Depends(get_services),
            db: Session = Depends(get_db)) -> dict[str, Any]:
    impact = ops.inject_weather(svc, db, body)
    svc.schedule_run("weather_change", background)
    return {"worsened": impact.worsened, "impact": impact.model_dump(mode="json")}


@router.post("/shelter")
def shelter(body: ShelterSim, background: BackgroundTasks, svc: Services = Depends(get_services),
            db: Session = Depends(get_db), actor: str = Depends(get_actor)) -> dict[str, Any]:
    impact = ops.update_shelter(svc, db, body.shelter_id, s.ShelterPatch(**body.model_dump(exclude={"shelter_id"})), actor)
    return {"replanning": replan_if_needed(svc, impact, "resource_change", background)}


@router.post("/resource")
def resource(body: ResourceSim, background: BackgroundTasks, svc: Services = Depends(get_services),
             db: Session = Depends(get_db), actor: str = Depends(get_actor)) -> dict[str, Any]:
    impact = ops.change_resource_status(svc, db, body.resource_id,
                                        s.ResourceStatusPatch(**body.model_dump(exclude={"resource_id"})), actor)
    return {"replanning": replan_if_needed(svc, impact, "resource_change", background)}


@router.post("/incident")
def incident(body: IncidentSim, background: BackgroundTasks, svc: Services = Depends(get_services),
             db: Session = Depends(get_db)) -> dict[str, Any]:
    sim.apply_step(svc, db, IncidentUpdateStep(kind="incident_update", at_minute=0, note="API", incident_id=body.incident_id,
                                               changes=body.changes, provenance=body.provenance))
    svc.schedule_run("reassess", background)
    return {"status": "updated"}


@router.get("/scenarios")
def scenarios() -> dict[str, Any]:
    return {tc: {"title": spec["title"], "scenario": spec["scenario"]} for tc, spec in sim.TEST_CASES.items()}


@router.post("/scenario/{tc_id}")
def scenario(tc_id: str, svc: Services = Depends(get_services)) -> dict[str, Any]:
    """Reset, set up the state for a test case, and run the agents once."""
    return sim.setup_test_case(svc, tc_id)
