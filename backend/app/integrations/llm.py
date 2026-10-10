"""LLM integration (Gemini or OpenAI): the ONLY place the LLM is called.

Providers (LLM_PROVIDER):
- gemini: Google Gemini through its OpenAI-compatible endpoint, using the Chat Completions API
  (`chat.completions.parse` with a Pydantic response_format for structured output). Key: GEMINI_API_KEY.
- openai: OpenAI Responses API (`responses.parse`). Key: LLM_API_KEY.
- none:   deterministic fallback only.
Both providers go through the same `openai` SDK, so no extra dependency is needed.

The LLM is used for exactly four things (design rule 1):
  extract_incident  - structured fields from free text      (structured output -> ExtractedIncident)
  classify_threat   - threat-to-life labels                 (structured output -> ThreatClassification)
  explain           - human-readable rewording of a recommendation
  sitrep_narrative  - situation-report narrative

Graceful degradation (design rule 8): if LLM_PROVIDER is "none", the key is missing, or a call
fails or times out (LLM_TIMEOUT_S, default 10 s), every method returns the deterministic fallback
(regex extractor / template text) with llm_used=False and confidence "low". Nothing here raises to callers.

Free text is grounding-checked: any ID or number not present in the input context -> discarded,
template used instead (grounded=False is recorded for the agent trace).

The API key is read from Settings only. It is never logged, printed or included in error messages.
"""

import json
import logging
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.core.config import Settings, get_settings
from app.integrations import fallback_extract as fx
from app.integrations.grounding import check_grounding
from app.models.schemas import Confidence, IncidentType
from app.models.vocab import AssistanceLabel, InfrastructureLabel, ThreatLabel

log = logging.getLogger(__name__)
PROMPT_DIR = Path(__file__).resolve().parents[1] / "agents" / "prompts"


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text()


# ---------------------------------------------------------------- structured-output schemas
# OpenAI strict structured outputs need every field present (null allowed) and no extra keys.

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtractedIncident(_Strict):
    type: IncidentType | None
    title: str
    zone: str | None
    location_text: str | None
    lat: float | None
    lng: float | None
    people_affected: int | None
    assistance_requested: list[AssistanceLabel]
    infrastructure_issues: list[InfrastructureLabel]
    threat_indicators: list[ThreatLabel]
    missing_fields: list[str]


class ThreatClassification(_Strict):
    threat_indicators: list[ThreatLabel]


# ---------------------------------------------------------------- results (always returned, never raised)

class LLMMeta(BaseModel):
    """What the agent trace records about one call."""

    llm_used: bool
    model: str | None = None
    duration_ms: int = 0
    error: str | None = None  # exception class + short reason, never request contents or the key


class ExtractionResult(LLMMeta):
    extracted: ExtractedIncident
    confidence: Confidence


class ThreatResult(LLMMeta):
    threat_indicators: list[str]


class TextResult(LLMMeta):
    text: str
    grounded: bool = True
    grounding_problems: list[str] = []


ExplainStyle = Literal["priority_reasons", "plan_explanation", "action_rationale"]


# ---------------------------------------------------------------- deterministic fallbacks

def fallback_extract(text: str) -> ExtractedIncident:
    incident_type = fx.extract_type(text)
    zone = fx.extract_zone(text)
    people = fx.extract_people(text)
    coords = fx.extract_coordinates(text)
    title_type = (incident_type or "incident").replace("_", " ").capitalize()
    missing = [name for name, value in (("type", incident_type), ("zone", zone), ("people_affected", people)) if value is None]
    if zone is None and coords is None:
        missing.append("location")
    return ExtractedIncident(
        type=incident_type, title=f"{title_type} report{f' in {zone}' if zone else ''}", zone=zone,
        location_text=None, lat=coords[0] if coords else None, lng=coords[1] if coords else None,
        people_affected=people, assistance_requested=fx.extract_assistance(text),
        infrastructure_issues=fx.extract_infrastructure(text), threat_indicators=fx.extract_threats(text),
        missing_fields=missing,
    )


def _bullets(items: list[str]) -> str:
    return "; ".join(items) if items else "none"


def template_explanation(context: dict[str, Any], style: ExplainStyle) -> str:
    if style == "priority_reasons":
        return " ".join(f"{r}." for r in context.get("reasons", [])) or "No contributing factors recorded."
    if style == "plan_explanation":
        allocs = context.get("allocations", [])
        lines = [f"{context.get('title', 'Plan')}: {context.get('strategy', '')}"]
        for a in allocs:
            res = ", ".join(r["resource_id"] for r in a.get("resources", [])) or "no resources"
            lines.append(f"{a['incident_id']} ({a.get('priority')}): {res} recommended.")
        for s in context.get("shortages", []):
            lines.append(f"Shortage of {s['resource_type']}: {s['required']} required, {s['available']} available "
                         f"(deficit {s['deficit']}). {s['note']}")
        lines.append("All allocations are recommendations pending coordinator approval.")
        return " ".join(lines)
    # action_rationale
    return (f"Recommended: {context.get('description', '')}. Rationale: {_bullets(context.get('rationale', []))}. "
            f"Assumptions: {_bullets(context.get('assumptions', []))}. Requires coordinator approval.")


def template_sitrep(snapshot: dict[str, Any]) -> str:
    incidents = snapshot.get("incidents", [])
    critical = [i["id"] for i in incidents if (i.get("priority") or {}).get("level") == "critical"]
    pending = [a["id"] for a in snapshot.get("actions", []) if a.get("status") == "pending"]
    parts = [f"{len(incidents)} active incidents; critical: {_bullets(critical)}."]
    for s in snapshot.get("shortages", []):
        parts.append(f"Shortage of {s['resource_type']}: deficit {s['deficit']}.")
    parts.append(f"Actions awaiting coordinator approval: {_bullets(pending)}.")
    return " ".join(parts)


def count_supported(value: int, text: str) -> bool:
    """True if the text states this people count: the regex extractor finds it, or the number appears
    within three words before a people word ("40 residents", "12 people stranded"). "20 houses" is not a count."""
    if fx.extract_people(text) == value:
        return True
    return re.search(rf"\b{value}\b(?:\s+\w+){{0,3}}?\s+{fx.PEOPLE_WORDS}\b", text, re.I) is not None


# ---------------------------------------------------------------- client

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEFAULT_MODELS = {"gemini": "gemini-3.5-flash-lite", "openai": "gpt-4o-mini"}
KEY_NAMES = {"gemini": "GEMINI_API_KEY", "openai": "LLM_API_KEY"}


class LLMClient:
    def __init__(self, settings: Settings | None = None, client: Any = None):
        self.settings = settings or get_settings()
        self.provider = self.settings.llm_provider
        self.model = self.settings.llm_model or DEFAULT_MODELS.get(self.provider, "gpt-4o-mini")
        self._client = client
        if self._client is None and self.enabled:
            from openai import OpenAI  # imported lazily so the fallback path never needs the SDK

            # max_retries=0: a slow LLM must not stall the demo; we fall back instead.
            self._client = OpenAI(api_key=self.settings.active_llm_key, timeout=self.settings.llm_timeout_s,
                                  max_retries=0, base_url=GEMINI_BASE_URL if self.provider == "gemini" else None)

    @property
    def enabled(self) -> bool:
        return self.provider in DEFAULT_MODELS and bool(self.settings.active_llm_key)

    def _disabled_reason(self) -> str:
        if self.provider not in DEFAULT_MODELS:
            return f"LLM_PROVIDER={self.provider}"
        return f"{KEY_NAMES[self.provider]} not set"

    def _call(self, fn) -> tuple[Any, LLMMeta]:
        """Run one SDK call, converting any failure into metadata instead of an exception."""
        if self._client is None:
            return None, LLMMeta(llm_used=False, error=self._disabled_reason())
        start = time.perf_counter()
        try:
            out = fn(self._client)
            return out, LLMMeta(llm_used=True, model=self.model, duration_ms=int((time.perf_counter() - start) * 1000))
        except Exception as exc:  # noqa: BLE001 - any SDK/network error must degrade, not crash
            reason = f"{type(exc).__name__}"
            log.warning("LLM call failed (%s); using deterministic fallback", reason)
            return None, LLMMeta(llm_used=False, model=self.model, error=reason,
                                 duration_ms=int((time.perf_counter() - start) * 1000))

    def _messages(self, instructions: str, user_input: str) -> list[dict[str, str]]:
        return [{"role": "system", "content": instructions}, {"role": "user", "content": user_input}]

    def _parse(self, instructions: str, user_input: str, schema: type[BaseModel]):
        if self.provider == "gemini":  # OpenAI-compatible Chat Completions with a JSON-schema response_format
            return self._call(lambda c: c.chat.completions.parse(
                model=self.model, messages=self._messages(instructions, user_input), response_format=schema,
                temperature=0,
            ).choices[0].message.parsed)
        return self._call(lambda c: c.responses.parse(
            model=self.model, instructions=instructions, input=user_input, text_format=schema, temperature=0,
        ).output_parsed)

    def _text(self, instructions: str, user_input: str):
        if self.provider == "gemini":
            return self._call(lambda c: c.chat.completions.create(
                model=self.model, messages=self._messages(instructions, user_input), temperature=0,
                max_tokens=600,
            ).choices[0].message.content)
        return self._call(lambda c: c.responses.create(
            model=self.model, instructions=instructions, input=user_input, temperature=0, max_output_tokens=600,
        ).output_text)

    # -------------------------------------------------------------- public interface

    def extract_incident(self, text: str) -> ExtractionResult:
        parsed, meta = self._parse(load_prompt("extraction"), f"Report:\n{text}", ExtractedIncident)
        if parsed is None:
            return ExtractionResult(**meta.model_dump(), extracted=fallback_extract(text), confidence="low")
        # Deterministic post-check: the model may not supply a count the text never states.
        if parsed.people_affected is not None and not count_supported(parsed.people_affected, text):
            parsed.people_affected = None
            parsed.missing_fields = list(dict.fromkeys([*parsed.missing_fields, "people_affected"]))
        confidence: Confidence = "low" if parsed.zone is None or parsed.type is None else "medium"
        return ExtractionResult(**meta.model_dump(), extracted=parsed, confidence=confidence)

    def classify_threat(self, text: str) -> ThreatResult:
        parsed, meta = self._parse(load_prompt("threat_classification"), f"Report:\n{text}", ThreatClassification)
        labels = parsed.threat_indicators if parsed else fx.extract_threats(text)
        return ThreatResult(**meta.model_dump(), threat_indicators=list(dict.fromkeys(labels)))

    def _grounded_text(self, prompt: str, context: dict[str, Any], template: str) -> TextResult:
        payload = json.dumps(context, default=str)
        text, meta = self._text(load_prompt(prompt), f"Context JSON:\n{payload}")
        if not text:
            return TextResult(**meta.model_dump(), text=template)
        problems = check_grounding(text, payload)
        if problems:
            log.warning("LLM text failed grounding check (%d problems); using template", len(problems))
            return TextResult(**meta.model_dump(), text=template, grounded=False, grounding_problems=problems)
        return TextResult(**meta.model_dump(), text=text.strip())

    def explain(self, context: dict[str, Any], style: ExplainStyle) -> TextResult:
        return self._grounded_text("explanation", context, template_explanation(context, style))

    def sitrep_narrative(self, snapshot: dict[str, Any]) -> TextResult:
        return self._grounded_text("sitrep", snapshot, template_sitrep(snapshot))


@lru_cache
def get_llm() -> LLMClient:
    return LLMClient()
