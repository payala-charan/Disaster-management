"""TC-01...TC-08 as pytest integration tests (PostGIS test database, simulation modes, LLM mocked).

The test cases themselves live in app/tools/test_cases.py so the live report script runs exactly the
same steps and checks.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.integrations.llm import LLMClient
from app.main import create_app
from app.tools.test_cases import TEST_CASES
from tests.graph.conftest import FakeOpenAIResponses

pytestmark = pytest.mark.db


@pytest.fixture
def client(session_factory, tmp_path):
    settings = Settings(_env_file=None, database_url="unused", weather_mode="simulation", routing_mode="simulation",
                        llm_provider="openai", llm_api_key="sk-test-not-real", llm_model="fake-model",
                        reports_dir=tmp_path)
    llm = LLMClient(settings, client=SimpleNamespace(responses=FakeOpenAIResponses()))
    app = create_app(settings=settings, session_factory=session_factory, llm=llm, background_checks=False)
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("case", TEST_CASES, ids=[fn.__name__.upper() for fn in TEST_CASES])
def test_case(client, case):
    result = case(client)
    failed = [f"{name}: {detail}" for name, ok, detail in result.checks if not ok]
    assert result.passed, f"{result.tc} failed:\n  " + "\n  ".join(failed)
    assert result.agents, "agent trace should be recorded"
