"""Controlled vocabularies for extracted labels.

The LLM's structured output is restricted to these values (they become JSON-schema enums), the
fallback extractor emits the same labels, and the engines' rule tables key on them. One list, three users.
"""

from typing import Literal, get_args

ThreatLabel = Literal[
    "trapped", "stranded", "injured", "water_rescue", "isolated", "cut_off",
    "evacuation_needed", "property_damage_only", "fire",
]
AssistanceLabel = Literal[
    "water_rescue", "evacuation", "medical_check", "medical_transport", "first_aid",
    "fire_suppression", "search_and_rescue", "shelter", "food_water",
]
InfrastructureLabel = Literal[
    "main_road_closed", "road_partially_flooded", "road_blocked", "power_outage", "utility_loss",
    "water_supply_loss", "bridge_damaged", "building_damage", "communications_down",
]

THREAT_LABELS: tuple[str, ...] = get_args(ThreatLabel)
ASSISTANCE_LABELS: tuple[str, ...] = get_args(AssistanceLabel)
INFRASTRUCTURE_LABELS: tuple[str, ...] = get_args(InfrastructureLabel)
