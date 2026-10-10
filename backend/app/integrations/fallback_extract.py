"""Deterministic regex/keyword extractor: the fallback used when the LLM is unavailable.

Deliberately conservative: it only fills a field when a clear pattern matches. Results are always
marked confidence "low" by the caller.

People count: only an explicit number followed by a people word (people, persons, residents,
individuals, villagers, children, adults) counts. "About 25 people" -> 25 (as reported).
When several counts are stated ("2 people trapped ... 15 residents need relocation") the largest is
used, since people_affected means everyone affected and smaller groups are usually part of it.
Ranges ("10-15 people"), families and houses are NOT converted into a count: the value stays null.
"""

import re

from app.models.vocab import AssistanceLabel, InfrastructureLabel, ThreatLabel

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "hundred": 100,
}
PEOPLE_WORDS = r"(?:people|persons|residents|individuals|villagers|children|adults|occupants)"
_NUM = r"(\d+|" + "|".join(NUMBER_WORDS) + r")"
PEOPLE_RE = re.compile(rf"\b{_NUM}\s+(?:\w+\s+)?{PEOPLE_WORDS}\b", re.I)
RANGE_RE = re.compile(rf"\b\d+\s*(?:-|–|to)\s*\d+\s+(?:\w+\s+)?{PEOPLE_WORDS}\b", re.I)
ZONE_RE = re.compile(r"\bzone\s+([a-z])\b", re.I)
COORD_RE = re.compile(r"(-?\d{1,2}\.\d{3,})\s*[, ]\s*(-?\d{1,3}\.\d{3,})")

# Ordered: the first matching rule wins (specific hazards before generic "water").
TYPE_RULES: list[tuple[str, str]] = [
    ("wildfire", r"\b(wildfire|forest fire|bush fire)\b"),
    ("landslide", r"\b(landslide|mudslide|land slide|debris flow)\b"),
    ("earthquake", r"\b(earthquake|tremor|quake)\b"),
    ("cyclone", r"\b(cyclone|hurricane|storm surge)\b"),
    ("industrial_accident", r"\b(chemical|gas leak|explosion|factory|industrial)\b"),
    ("urban_emergency", r"\b(fire|smoke|blaze|building collapse)\b"),
    ("flood", r"\b(flood\w*|water levels?|submerged|inundat\w*|water (?:is )?rising|rising water)\b"),
]

THREAT_RULES: list[tuple[ThreatLabel, str]] = [
    ("trapped", r"\btrapped\b"),
    ("stranded", r"\b(stranded|stuck|on (?:the )?roofs?|rooftops?)\b"),
    ("injured", r"\b(injur\w*|hurt|bleeding|breathing difficult\w*|unconscious)\b"),
    ("cut_off", r"\bcut off\b"),
    ("isolated", r"\bisolated\b"),
    ("evacuation_needed", r"\bevacuat\w*\b"),
    ("fire", r"\b(fire|smoke|blaze)\b"),
]

ASSISTANCE_RULES: list[tuple[AssistanceLabel, str]] = [
    ("water_rescue", r"\b(boats?|water rescue)\b"),
    ("evacuation", r"\bevacuat\w*\b"),
    ("medical_check", r"\b(medical|injur\w*|ambulance|doctor|breathing difficult\w*)\b"),
    ("fire_suppression", r"\b(fire|smoke|blaze)\b"),
    ("search_and_rescue", r"\b(trapped|under debris)\b"),
    ("shelter", r"\bshelter\b"),
]

INFRA_RULES: list[tuple[InfrastructureLabel, str]] = [
    ("main_road_closed", r"\bmain (?:access )?road\b[^.]*\bclosed\b"),
    ("road_partially_flooded", r"\broad\b[^.]*\bpartially flooded\b"),
    ("road_blocked", r"\b(cars? cannot|impassable|road\b[^.]*\bblocked|blocked by debris)\b"),
    ("power_outage", r"\b(power outage|no electricity|power cut|power lines? down)\b"),
    ("bridge_damaged", r"\bbridge\b[^.]*\b(damaged|collapsed|washed away)\b"),
    ("building_damage", r"\b(houses?|buildings?)\s+(?:\w+\s+)?damaged\b"),
]


def _labels(rules, text: str) -> list:
    return [label for label, pattern in rules if re.search(pattern, text, re.I)]


def extract_people(text: str) -> int | None:
    if RANGE_RE.search(text):
        return None
    counts = [int(raw) if raw.isdigit() else NUMBER_WORDS[raw.lower()] for raw in PEOPLE_RE.findall(text)]
    return max(counts) if counts else None


def extract_type(text: str) -> str | None:
    for incident_type, pattern in TYPE_RULES:
        if re.search(pattern, text, re.I):
            return incident_type
    return None


def extract_zone(text: str) -> str | None:
    m = ZONE_RE.search(text)
    return f"Zone {m.group(1).upper()}" if m else None


def extract_coordinates(text: str) -> tuple[float, float] | None:
    m = COORD_RE.search(text)
    if not m:
        return None
    lat, lng = float(m.group(1)), float(m.group(2))
    return (lat, lng) if -90 <= lat <= 90 and -180 <= lng <= 180 else None


def extract_threats(text: str) -> list[ThreatLabel]:
    labels = _labels(THREAT_RULES, text)
    if "trapped" in labels and "stranded" in labels and not re.search(r"\b(stranded|roofs?|rooftops?)\b", text, re.I):
        labels.remove("stranded")  # "stuck" next to "trapped" is the same fact, not a second one
    return labels


def extract_assistance(text: str) -> list[AssistanceLabel]:
    return _labels(ASSISTANCE_RULES, text)


def extract_infrastructure(text: str) -> list[InfrastructureLabel]:
    labels = _labels(INFRA_RULES, text)
    if "main_road_closed" in labels and "road_blocked" in labels:
        labels.remove("road_blocked")
    return labels
