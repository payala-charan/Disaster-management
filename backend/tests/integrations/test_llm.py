"""LLM client with a FAKE OpenAI client: success paths, failures, fallbacks and grounding."""

import logging
from types import SimpleNamespace

import openai
import pytest

from app.core.config import Settings
from app.integrations.llm import ExtractedIncident, LLMClient, ThreatClassification, load_prompt

SECRET = "sk-test-SECRET-should-never-appear"


def settings(**kw) -> Settings:
    base = dict(llm_provider="openai", llm_api_key=SECRET, llm_model="test-model")
    return Settings(_env_file=None, **(base | kw))


class FakeResponses:
    """Records calls; returns queued results or raises queued exceptions."""

    def __init__(self, parsed=None, text=None, error: Exception | None = None):
        self.parsed, self.text, self.error, self.calls = parsed, text, error, []

    def parse(self, **kwargs):
        self.calls.append(("parse", kwargs))
        if self.error:
            raise self.error
        return SimpleNamespace(output_parsed=self.parsed)

    def create(self, **kwargs):
        self.calls.append(("create", kwargs))
        if self.error:
            raise self.error
        return SimpleNamespace(output_text=self.text)


def client(**kw) -> tuple[LLMClient, FakeResponses]:
    fake = FakeResponses(**kw)
    return LLMClient(settings(), client=SimpleNamespace(responses=fake)), fake


def extracted(**kw) -> ExtractedIncident:
    base = dict(type="flood", title="River Street flooding", zone="Zone D", location_text="River Street",
                lat=None, lng=None, people_affected=25, assistance_requested=["evacuation"],
                infrastructure_issues=["road_blocked"], threat_indicators=["injured"], missing_fields=[])
    return ExtractedIncident(**(base | kw))


REPORT = "Flood water entered homes on River Street in Zone D. About 25 people need help to evacuate."


# ---------------------------------------------------------------- disabled -> fallback

@pytest.mark.parametrize("kw, reason", [
    ({"llm_provider": "none"}, "LLM_PROVIDER=none"),
    ({"llm_api_key": None}, "LLM_API_KEY not set"),
])
def test_disabled_llm_uses_fallback(kw, reason):
    llm = LLMClient(settings(**kw))  # no client is created, so the OpenAI guard is never hit
    result = llm.extract_incident(REPORT)
    assert not result.llm_used and result.confidence == "low" and result.error == reason
    assert (result.extracted.type, result.extracted.zone, result.extracted.people_affected) == ("flood", "Zone D", 25)


# ---------------------------------------------------------------- structured extraction

def test_extraction_uses_structured_output_with_prompt_and_schema():
    llm, fake = client(parsed=extracted())
    result = llm.extract_incident(REPORT)
    assert result.llm_used and result.model == "test-model" and result.confidence == "medium"
    kind, kwargs = fake.calls[0]
    assert kind == "parse" and kwargs["text_format"] is ExtractedIncident
    assert kwargs["instructions"] == load_prompt("extraction") and kwargs["temperature"] == 0
    assert REPORT in kwargs["input"]


def test_llm_count_not_stated_in_text_is_discarded():
    llm, _ = client(parsed=extracted(people_affected=20))
    result = llm.extract_incident("Flood water has entered approximately 20 houses in Zone A.")
    assert result.extracted.people_affected is None
    assert "people_affected" in result.extracted.missing_fields


def test_missing_zone_gives_low_confidence():
    llm, _ = client(parsed=extracted(zone=None))
    assert llm.extract_incident(REPORT).confidence == "low"


@pytest.mark.parametrize("error", [
    openai.APITimeoutError(request=None),
    openai.APIConnectionError(request=None),
    RuntimeError("boom"),
])
def test_api_failure_falls_back_and_never_leaks_the_key(error, caplog):
    llm, _ = client(error=error)
    with caplog.at_level(logging.DEBUG):
        result = llm.extract_incident(REPORT)
    assert not result.llm_used and result.confidence == "low"
    assert result.error == type(error).__name__
    assert result.extracted.zone == "Zone D"  # deterministic fallback still extracted it
    assert SECRET not in caplog.text and SECRET not in result.model_dump_json()


def test_settings_repr_hides_the_key():
    assert SECRET not in repr(settings()) and SECRET not in str(settings())


def test_classify_threat_success_and_fallback():
    llm, _ = client(parsed=ThreatClassification(threat_indicators=["stranded", "stranded"]))
    assert llm.classify_threat("people on roofs").threat_indicators == ["stranded"]
    off = LLMClient(settings(llm_provider="none"))
    assert off.classify_threat("2 people trapped under debris").threat_indicators == ["trapped"]


def test_strict_schema_shape():
    """OpenAI strict mode: every property required, no additional properties, enums for labels."""
    schema = ExtractedIncident.model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert "stranded" in str(schema["properties"]["threat_indicators"])


# ---------------------------------------------------------------- free text + grounding

PLAN_CONTEXT = {
    "title": "Plan B: Coverage", "strategy": "Minimum response first.",
    "allocations": [{"incident_id": "INC-003", "priority": "critical",
                     "resources": [{"resource_id": "BOAT-002"}, {"resource_id": "TEAM-002"}]}],
    "shortages": [{"resource_type": "rescue_boat", "required": 16, "available": 4, "deficit": 12,
                   "note": "Deficit of 12 rescue boats. Escalate."}],
}


def test_grounded_explanation_is_kept():
    text = "BOAT-002 and TEAM-002 are recommended for INC-003. Boats are short by 12 (16 required, 4 available)."
    llm, fake = client(text=text)
    result = llm.explain(PLAN_CONTEXT, "plan_explanation")
    assert result.llm_used and result.grounded and result.text == text
    assert fake.calls[0][1]["instructions"] == load_prompt("explanation")


@pytest.mark.parametrize("hallucination", [
    "BOAT-009 is recommended for INC-003.",              # resource that does not exist
    "BOAT-002 can evacuate 120 people from INC-003.",    # number not in the context
])
def test_ungrounded_text_is_replaced_by_template(hallucination):
    llm, _ = client(text=hallucination)
    result = llm.explain(PLAN_CONTEXT, "plan_explanation")
    assert not result.grounded and result.grounding_problems
    assert result.text.startswith("Plan B: Coverage") and "deficit 12" in result.text


def test_template_explanation_when_disabled():
    result = LLMClient(settings(llm_provider="none")).explain(PLAN_CONTEXT, "plan_explanation")
    assert not result.llm_used
    assert "INC-003 (critical): BOAT-002, TEAM-002 recommended." in result.text
    assert "pending coordinator approval" in result.text


def test_sitrep_fallback_template():
    snapshot = {"incidents": [{"id": "INC-003", "priority": {"level": "critical"}}],
                "actions": [{"id": "ACT-002", "status": "pending"}], "shortages": []}
    result = LLMClient(settings(llm_provider="none")).sitrep_narrative(snapshot)
    assert "critical: INC-003" in result.text and "ACT-002" in result.text


@pytest.mark.parametrize("prompt", ["extraction", "threat_classification", "explanation", "sitrep"])
def test_prompts_forbid_invention(prompt):
    text = load_prompt(prompt).lower()
    assert "only" in text
    assert "do not add" in text or "never invent" in text or "do not assume" in text


# ---------------------------------------------------------------- Gemini (OpenAI-compatible Chat Completions)

GEMINI_SECRET = "gemini-test-SECRET-should-never-appear"


class FakeChatCompletions:
    def __init__(self, parsed=None, text=None, error: Exception | None = None):
        self.parsed, self.text, self.error, self.calls = parsed, text, error, []

    def _reply(self, **message):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(**message))])

    def parse(self, **kwargs):
        self.calls.append(("parse", kwargs))
        if self.error:
            raise self.error
        return self._reply(parsed=self.parsed)

    def create(self, **kwargs):
        self.calls.append(("create", kwargs))
        if self.error:
            raise self.error
        return self._reply(content=self.text)


def gemini(**kw) -> tuple[LLMClient, FakeChatCompletions]:
    fake = FakeChatCompletions(**kw)
    s = Settings(_env_file=None, llm_provider="gemini", gemini_api_key=GEMINI_SECRET, llm_model=None)
    return LLMClient(s, client=SimpleNamespace(chat=SimpleNamespace(completions=fake))), fake


def test_gemini_extraction_uses_chat_completions_structured_output():
    llm, fake = gemini(parsed=extracted())
    result = llm.extract_incident(REPORT)
    assert result.llm_used and result.model == "gemini-3.5-flash-lite" and result.extracted.people_affected == 25
    kind, kwargs = fake.calls[0]
    assert kind == "parse" and kwargs["response_format"] is ExtractedIncident and kwargs["temperature"] == 0
    assert kwargs["messages"][0] == {"role": "system", "content": load_prompt("extraction")}
    assert REPORT in kwargs["messages"][1]["content"]


def test_gemini_text_is_grounding_checked():
    llm, fake = gemini(text="BOAT-009 is recommended for INC-003.")
    result = llm.explain(PLAN_CONTEXT, "plan_explanation")
    assert fake.calls[0][0] == "create" and not result.grounded and result.text.startswith("Plan B: Coverage")


def test_gemini_failure_falls_back_without_leaking_key(caplog):
    llm, _ = gemini(error=RuntimeError("401 invalid key"))
    with caplog.at_level(logging.DEBUG):
        result = llm.extract_incident(REPORT)
    assert not result.llm_used and result.confidence == "low" and result.extracted.zone == "Zone D"
    assert GEMINI_SECRET not in caplog.text and GEMINI_SECRET not in result.model_dump_json()


@pytest.mark.parametrize("kw, key", [
    ({"llm_provider": "gemini", "gemini_api_key": "g", "llm_api_key": "o"}, "g"),
    ({"llm_provider": "gemini", "llm_api_key": "o"}, "o"),          # LLM_API_KEY also accepted for Gemini
    ({"llm_provider": "openai", "gemini_api_key": "g", "llm_api_key": "o"}, "o"),
    ({"llm_provider": "none", "gemini_api_key": "g"}, None),
])
def test_active_key_follows_provider(kw, key):
    assert Settings(_env_file=None, **kw).active_llm_key == key


def test_gemini_client_points_at_gemini_endpoint(monkeypatch):
    seen = {}

    class Recorder:
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(openai, "OpenAI", Recorder)
    LLMClient(Settings(_env_file=None, llm_provider="gemini", gemini_api_key=GEMINI_SECRET))
    assert seen["base_url"].startswith("https://generativelanguage.googleapis.com/") and seen["max_retries"] == 0
    assert LLMClient(Settings(_env_file=None, llm_provider="gemini")).extract_incident(REPORT).error == "GEMINI_API_KEY not set"


def test_settings_repr_hides_gemini_key():
    assert GEMINI_SECRET not in repr(Settings(_env_file=None, gemini_api_key=GEMINI_SECRET))
