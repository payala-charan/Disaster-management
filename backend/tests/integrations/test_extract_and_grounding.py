import pytest

from app.integrations import fallback_extract as fx
from app.integrations.grounding import check_grounding


@pytest.mark.parametrize("text, people", [
    ("About 25 people need help", 25),
    ("Around 40 residents may require evacuation", 40),
    ("12 people stranded on rooftops", 12),
    ("twelve people on a roof", 12),
    ("2 people trapped under debris, about 15 residents need relocation", 15),
    ("Saw maybe 10-15 people on roofs", None),           # range -> unknown, never averaged
    ("about 6 families need help", None),                # families are not people
    ("Flood water has entered approximately 20 houses", None),
    ("lots of people stuck", None),
])
def test_people_count(text, people):
    assert fx.extract_people(text) == people


@pytest.mark.parametrize("text, incident_type", [
    ("flood water entered homes", "flood"),
    ("water levels rose quickly", "flood"),
    ("landslide on the hill road", "landslide"),
    ("fire in a commercial building, heavy smoke", "urban_emergency"),
    ("forest fire spreading", "wildfire"),
    ("gas leak at the factory", "industrial_accident"),
    ("something happened", None),
])
def test_incident_type(text, incident_type):
    assert fx.extract_type(text) == incident_type


def test_zone_and_coordinates():
    assert fx.extract_zone("near the market in zone d!!") == "Zone D"
    assert fx.extract_zone("no zone given") is None
    assert fx.extract_coordinates("at 13.0362, 80.2075 by the bridge") == (13.0362, 80.2075)
    assert fx.extract_coordinates("reading 2.4 m") is None


def test_labels():
    text = "Main road closed. 2 people trapped, one woman injured. Need a boat to evacuate. Power outage."
    assert set(fx.extract_threats(text)) == {"trapped", "injured", "evacuation_needed"}
    assert set(fx.extract_assistance(text)) >= {"water_rescue", "evacuation", "medical_check", "search_and_rescue"}
    assert set(fx.extract_infrastructure(text)) == {"main_road_closed", "power_outage"}


def test_labels_are_in_vocabulary():
    from app.models.vocab import ASSISTANCE_LABELS, INFRASTRUCTURE_LABELS, THREAT_LABELS

    assert {label for label, _ in fx.THREAT_RULES} <= set(THREAT_LABELS)
    assert {label for label, _ in fx.ASSISTANCE_RULES} <= set(ASSISTANCE_LABELS)
    assert {label for label, _ in fx.INFRA_RULES} <= set(INFRASTRUCTURE_LABELS)


# ---------------------------------------------------------------- grounding

CONTEXT = {"incident": "INC-003", "people": 75, "boats": ["BOAT-002"], "threshold": 0.85, "rain": 24.5}


@pytest.mark.parametrize("text, problems", [
    ("INC-003: 75 people, BOAT-002 recommended.", []),
    ("Rain at 24.5 mm/h; shelter alert at 85%.", []),           # 85% <-> 0.85
    ("BOAT-003 recommended.", ["unknown ID BOAT-003"]),
    ("Around 80 people affected.", ["unsupported number 80"]),
    ("INC-003 needs help.", []),                                # the 003 inside the ID is not a number
])
def test_grounding(text, problems):
    assert check_grounding(text, CONTEXT) == problems
