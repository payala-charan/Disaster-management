"""Workflow test fixtures: simulation weather/routing, and a fake OpenAI client."""

from types import SimpleNamespace

import pytest

from app.agents.base import build_deps
from app.core.config import Settings
from app.integrations.llm import ExtractedIncident, LLMClient, ThreatClassification, fallback_extract
from app.integrations.fallback_extract import extract_threats


class FakeOpenAIResponses:
    """Answers like a well-behaved model, using the deterministic extractor, so tests exercise the
    LLM code path (llm_used=True) with predictable output. Set `fail` to simulate an outage."""

    def __init__(self, fail: Exception | None = None):
        self.fail, self.calls = fail, []

    def parse(self, *, input, text_format, **kw):
        self.calls.append(text_format.__name__)
        if self.fail:
            raise self.fail
        text = input.split("\n", 1)[1]
        if text_format is ExtractedIncident:
            return SimpleNamespace(output_parsed=fallback_extract(text))
        return SimpleNamespace(output_parsed=ThreatClassification(threat_indicators=extract_threats(text)))

    def create(self, **kw):
        self.calls.append("text")
        if self.fail:
            raise self.fail
        return SimpleNamespace(output_text="All allocations in this plan are recommendations pending coordinator approval.")


def make_settings(**kw) -> Settings:
    base = dict(weather_mode="simulation", routing_mode="simulation", llm_provider="openai",
                llm_api_key="sk-test-not-real", llm_model="fake-model")
    return Settings(_env_file=None, database_url="unused", **(base | kw))


@pytest.fixture
def fake_openai():
    return FakeOpenAIResponses()


@pytest.fixture
def deps(session_factory, fake_openai):
    settings = make_settings()
    llm = LLMClient(settings, client=SimpleNamespace(responses=fake_openai))
    return build_deps(session_factory=session_factory, settings=settings, llm=llm)
