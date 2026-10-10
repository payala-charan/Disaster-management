"""API test client bound to the PostGIS test database (simulation weather/routing, LLM fallback only)."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, database_url="unused", weather_mode="simulation", routing_mode="simulation",
                    llm_provider="none", reports_dir=tmp_path / "reports")


@pytest.fixture
def app(session_factory, settings):
    return create_app(settings=settings, session_factory=session_factory, background_checks=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:  # `with` runs the lifespan (event loop for WebSocket broadcasts)
        yield c


@pytest.fixture
def services(app):
    return app.state.services


@pytest.fixture
def seeded_run(client):
    """Seed scenario after one agent run: plans A/B/C and three pending actions."""
    result = client.post("/api/assessments/run").json()
    assert result["status"] == "completed"
    return result


def pending_for(client, incident_id: str) -> dict:
    return next(a for a in client.get("/api/actions?status=pending").json() if a["incident_id"] == incident_id)
