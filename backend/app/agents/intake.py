"""Agent 1: Incident Intake. Normalizes raw reports from five source types into incidents.

Per raw report (status 'received'):
1. Build the text to read: free text plus any descriptive payload fields (observation, message, ...).
2. Area-wide government advisories (payload with `areas` and no specific location) do not become rescue
   incidents: they become a warning alert, because inventing an incident or a people count is not allowed.
3. Extract fields: structured payload fields are used as given (an explicit `people_count: null` stays
   null); free text goes through the LLM (with regex fallback). IoT sensor events are mapped
   deterministically (water_level -> flood).
4. Geocode (coordinates -> zone gazetteer -> unknown). Unknown location stays None.
5. Confidence and verification are set by documented source rules:
     field_team / government_alert: confidence high if type and zone are known, verification 'verified'
       for field teams, otherwise 'pending'
     emergency_call: extractor confidence (max medium), 'pending'
     citizen_report and iot_sensor: confidence low, 'unverified'
6. Provenance: counts and places stated in the report are 'reported'; labels derived from free text
   (threat, assistance, infrastructure) and approximated locations are 'inferred'.
7. Duplicate detection against all active incidents; links are created for HUMAN review only.
8. Field-team road observations are logged as 'reported' condition changes. They never overwrite the
   road registry, which only holds known facts.
A report whose incident type cannot be determined is marked 'failed' and listed for manual review.
"""

from typing import Any

from sqlalchemy import select

from app.agents.base import Deps, StepRecorder, agent
from app.db import models as m
from app.db import repository as repo
from app.db.geo import point_to_geom
from app.engines.duplicates import find_candidates
from app.integrations.llm import ExtractedIncident
from app.models.schemas import Confidence
from app.models.vocab import ASSISTANCE_LABELS

TEXT_FIELDS = ("observation", "message", "description", "text", "details")


def report_text(raw: m.RawReport) -> str:
    parts = [raw.raw_text] if raw.raw_text else []
    payload = raw.raw_payload or {}
    parts += [str(payload[k]) for k in TEXT_FIELDS if payload.get(k)]
    if raw.source == "iot_sensor":
        parts.append(
            f"Sensor {payload.get('sensor_id')} ({payload.get('sensor_type')}) reading {payload.get('reading_m')} m, "
            f"alert threshold {payload.get('alert_threshold_m')} m, trend {payload.get('trend')}"
        )
    return " ".join(parts).strip()


def is_area_advisory(raw: m.RawReport) -> bool:
    p = raw.raw_payload or {}
    return raw.source == "government_alert" and bool(p.get("areas")) and not p.get("location") and not raw.raw_text


def sensor_extraction(raw: m.RawReport) -> ExtractedIncident:
    p = raw.raw_payload or {}
    is_water = p.get("sensor_type") == "water_level"
    return ExtractedIncident(
        type="flood" if is_water else None,
        title=f"Sensor {p.get('sensor_id')}: {p.get('sensor_type', 'reading')} {p.get('reading_m')} m "
              f"(threshold {p.get('alert_threshold_m')} m, {p.get('trend', 'trend unknown')})",
        zone=p.get("zone"), location_text=None, lat=None, lng=None, people_affected=None,
        assistance_requested=[], infrastructure_issues=[], threat_indicators=[],
        missing_fields=["people_affected", "threat_indicators"],
    )


def source_rules(source: str, extracted_conf: Confidence, has_type_and_zone: bool) -> tuple[Confidence, str]:
    if source in ("field_team", "government_alert"):
        conf: Confidence = "high" if has_type_and_zone else "medium"
        return conf, "verified" if source == "field_team" else "pending"
    if source == "emergency_call":
        return ("medium" if extracted_conf != "low" else "low"), "pending"
    return "low", "unverified"  # citizen_report, iot_sensor


def normalize(raw: m.RawReport, deps: Deps, rec: StepRecorder) -> tuple[dict[str, Any] | None, str | None]:
    """Return (incident fields, failure reason)."""
    text = report_text(raw)
    payload = raw.raw_payload or {}

    if raw.source == "iot_sensor":
        ext = rec.tool("sensor_mapping", {"sensor_id": payload.get("sensor_id")}, lambda: sensor_extraction(raw),
                       lambda e: f"type={e.type}")
        ext_conf: Confidence = "low"
    else:
        result = deps.llm.extract_incident(text)
        rec.llm("llm.extract_incident", {"report": raw.id, "chars": len(text)}, result,
                f"type={result.extracted.type} zone={result.extracted.zone} people={result.extracted.people_affected}")
        ext, ext_conf = result.extracted, result.confidence
        if not ext.threat_indicators and payload and text:
            threats = deps.llm.classify_threat(text)
            rec.llm("llm.classify_threat", {"report": raw.id}, threats, f"{threats.threat_indicators}")
            ext.threat_indicators = threats.threat_indicators

    # Structured payload fields are explicit statements: they override text extraction.
    location = payload.get("location") or {}
    people = payload["people_count"] if "people_count" in payload else ext.people_affected
    assistance = list(dict.fromkeys([*ext.assistance_requested,
                                     *[n for n in payload.get("needs", []) if n in ASSISTANCE_LABELS]]))
    zone_hint = payload.get("zone") or ext.zone

    geo = rec.tool("geocoder.resolve", {"zone": zone_hint, "has_coordinates": bool(location or ext.lat)},
                   lambda: deps.geocoder.resolve(zone=zone_hint, text=text, location_text=ext.location_text,
                                                 lat=location.get("lat", ext.lat), lng=location.get("lng", ext.lng)),
                   lambda g: f"{g.method}: zone={g.zone} provenance={g.provenance}")

    if ext.type is None:
        return None, f"{raw.id}: incident type could not be determined from the report: manual review required"

    conf, verification = source_rules(raw.source, ext_conf, ext.type is not None and geo.zone is not None)
    provenance = {"assistance_requested": "inferred", "threat_indicators": "inferred",
                  "infrastructure_issues": "inferred"}
    if people is not None:
        provenance["people_affected"] = "reported"
    if geo.provenance:
        provenance["location"] = geo.provenance
    if "needs" in payload:
        provenance["assistance_requested"] = "reported"

    notes = list(geo.verification_needed)
    if ext.missing_fields:
        notes.append(f"Missing: {', '.join(ext.missing_fields)}")
    supporting = "; ".join(notes)
    return {
        "type": ext.type, "title": ext.title[:200], "description": text or ext.title, "zone": geo.zone or "Unknown",
        "geom": point_to_geom(geo.location) if geo.location else None, "reported_at": raw.received_at,
        "source": raw.source, "people_affected": people, "assistance_requested": assistance,
        "infrastructure_issues": ext.infrastructure_issues, "threat_indicators": ext.threat_indicators,
        "confidence": conf, "verification_status": verification, "status": "active",
        "supporting_info": supporting or None, "field_provenance": provenance,
    }, None


@agent("incident_intake")
def incident_intake(state: dict[str, Any], deps: Deps, rec: StepRecorder, db) -> dict[str, Any]:
    raws = rec.tool("raw_reports.fetch_pending", {"status": "received"},
                    lambda: db.scalars(select(m.RawReport).where(m.RawReport.processing_status == "received")
                                       .order_by(m.RawReport.received_at, m.RawReport.id)).all(),
                    lambda rs: f"{len(rs)} pending report(s)")
    rec.input_summary = f"{len(raws)} new raw report(s): {', '.join(r.id for r in raws) or 'none'}"
    new_ids: list[str] = []
    candidates: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    errors: list[str] = []
    settings = deps.settings

    for raw in raws:
        if is_area_advisory(raw):
            p = raw.raw_payload or {}
            alerts.append({"kind": "weather_worsening", "severity": "warning", "related_id": None,
                           "message": f"{p.get('issuer', 'Government')} {p.get('alert_level', '')} alert for "
                                      f"{', '.join(p['areas'])}: {p.get('message', '')}".strip()})
            raw.processing_status = "normalized"
            continue

        fields, failure = normalize(raw, deps, rec)
        if failure:
            raw.processing_status = "failed"
            errors.append(failure)
            continue

        incident = m.Incident(id=repo.next_id(db, m.Incident, "INC"), **fields)
        db.add(incident)
        db.flush()
        raw.processing_status, raw.normalized_incident_id = "normalized", incident.id
        new_ids.append(incident.id)

        for obs in (raw.raw_payload or {}).get("road_observations", []):
            road = db.get(m.RoadSegment, obs.get("road_id"))
            if road and obs.get("observed_status"):
                changes.append({"entity": road.name, "entity_id": road.id, "field": "observed_status",
                                "old_value": road.status, "new_value": obs["observed_status"], "provenance": "reported"})

        facts = repo.incident_facts(db)
        found = rec.tool(
            "duplicates.find_candidates", {"incident": incident.id, "against": len(facts) - 1},
            lambda: find_candidates(facts[incident.id], [f for i, f in facts.items() if i != incident.id],
                                    settings.duplicate_distance_m, settings.duplicate_time_window_min),
            lambda c: ", ".join(f"{x.candidate_id} {x.classification} {x.score:.2f}" for x in c) or "none",
        )
        for cand in found:
            db.add(m.DuplicateLink(id=repo.next_id(db, m.DuplicateLink, "DUP"), incident_id=cand.incident_id,
                                   candidate_id=cand.candidate_id, score=cand.score,
                                   classification=cand.classification, breakdown=cand.breakdown.model_dump(),
                                   status="pending"))
            candidates.append(cand.model_dump(mode="json"))

    db.flush()
    incidents = repo.incident_facts(db)
    rec.output_summary = (f"Created {len(new_ids)} incident(s) {new_ids}; {len(candidates)} duplicate link(s); "
                          f"{len(errors)} report(s) need manual review; {len(incidents)} active incident(s)")
    return {
        "new_incident_ids": new_ids,
        "incidents": {k: v.model_dump(mode="json") for k, v in incidents.items()},
        "duplicate_candidates": candidates,
        "alerts_to_emit": alerts,
        "changes": changes,
        "errors": errors,
    }
