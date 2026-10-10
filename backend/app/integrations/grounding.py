"""Grounding check for LLM free text: every ID and number it mentions must exist in its input.

If the model mentions BOAT-009 or "120 people" and neither appears in the context it was given,
the text is discarded and the deterministic template is used instead.

Number matching:
- IDs are removed before numbers are extracted, so "INC-003" does not contribute "003".
- A percentage n% is accepted if n or n/100 appears in the context (0.85 <-> 85%).
Known limitation (documented): spelled-out numbers ("twelve") are not checked.
"""

import json
import re
from typing import Any

ID_RE = re.compile(r"\b(?:INC|BOAT|TEAM|AMB|VEH|MED|RD|SHL|ACT|PLAN|RUN|ALT|DUP|CHG|SR|RAW)-[A-Z0-9]+\b")
NUM_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(%?)")


def _numbers(text: str) -> list[tuple[float, bool]]:
    without_ids = ID_RE.sub(" ", text)
    return [(float(n), pct == "%") for n, pct in NUM_RE.findall(without_ids)]


def check_grounding(text: str, context: Any) -> list[str]:
    """Return the ungrounded IDs/numbers (empty list = grounded)."""
    source = context if isinstance(context, str) else json.dumps(context, default=str)
    known_ids = set(ID_RE.findall(source))
    known_nums = {n for n, _ in _numbers(source)}

    problems = [f"unknown ID {i}" for i in sorted(set(ID_RE.findall(text)) - known_ids)]
    for value, is_pct in _numbers(text):
        ok = value in known_nums or (is_pct and round(value / 100, 6) in known_nums)
        if not ok:
            problems.append(f"unsupported number {value:g}{'%' if is_pct else ''}")
    return list(dict.fromkeys(problems))
