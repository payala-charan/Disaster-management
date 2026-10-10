"""Duplicate detection between incident reports.

score = 0.35 x distance_score + 0.25 x time_score + 0.15 x type_match + 0.25 x text_similarity
- distance_score  = max(0, 1 - d / DUPLICATE_DISTANCE_M), d = haversine metres (0 if a location is unknown)
- time_score      = max(0, 1 - |dt| / DUPLICATE_TIME_WINDOW_MIN)
- type_match      = 1 if the incident types are equal, else 0
- text_similarity = rapidfuzz token_set_ratio(title + description, lowercased, punctuation stripped) / 100

Classification: score >= 0.70 -> potential_duplicate; 0.50-0.70 -> related; below 0.50 -> not linked.
The result only proposes a link for HUMAN review. Nothing is ever merged or deleted automatically.
"""

from rapidfuzz import fuzz, utils

from app.engines.geo import haversine_m
from app.engines.models import IncidentFacts
from app.models.schemas import Contract, DuplicateBreakdown

W_DISTANCE, W_TIME, W_TYPE, W_TEXT = 0.35, 0.25, 0.15, 0.25
POTENTIAL_DUPLICATE = 0.70
RELATED = 0.50


class DuplicateCandidate(Contract):
    incident_id: str   # the newer report
    candidate_id: str  # the existing incident
    score: float
    classification: str  # potential_duplicate | related
    breakdown: DuplicateBreakdown


def _text(i: IncidentFacts) -> str:
    return f"{i.title} {i.description}"


def score_pair(
    new: IncidentFacts, existing: IncidentFacts, distance_limit_m: float = 500, window_min: float = 60,
) -> tuple[float, DuplicateBreakdown]:
    if new.location and existing.location:
        d = haversine_m(new.location, existing.location)
        distance_score = max(0.0, 1 - d / distance_limit_m)
    else:
        d, distance_score = None, 0.0
    dt_min = abs((new.reported_at - existing.reported_at).total_seconds()) / 60
    time_score = max(0.0, 1 - dt_min / window_min)
    type_match = 1.0 if new.type == existing.type else 0.0
    # processor=default_process lowercases and strips punctuation; rapidfuzz does not do it by default.
    text = fuzz.token_set_ratio(_text(new), _text(existing), processor=utils.default_process) / 100

    score = W_DISTANCE * distance_score + W_TIME * time_score + W_TYPE * type_match + W_TEXT * text
    breakdown = DuplicateBreakdown(
        distance_m=round(d, 1) if d is not None else None,
        distance_score=round(distance_score, 3),
        time_diff_min=round(dt_min, 1),
        time_score=round(time_score, 3),
        type_match=type_match,
        text_similarity=round(text, 3),
    )
    return round(score, 3), breakdown


def classify(score: float) -> str | None:
    if score >= POTENTIAL_DUPLICATE:
        return "potential_duplicate"
    if score >= RELATED:
        return "related"
    return None


def find_candidates(
    new: IncidentFacts, existing: list[IncidentFacts], distance_limit_m: float = 500, window_min: float = 60,
) -> list[DuplicateCandidate]:
    """All existing incidents scoring >= 0.50 against `new`, best first."""
    found = []
    for other in existing:
        if other.id == new.id:
            continue
        score, breakdown = score_pair(new, other, distance_limit_m, window_min)
        label = classify(score)
        if label:
            found.append(DuplicateCandidate(incident_id=new.id, candidate_id=other.id, score=score,
                                            classification=label, breakdown=breakdown))
    return sorted(found, key=lambda c: (-c.score, c.candidate_id))
