import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# backend/ — anchor .env here so it loads no matter which directory a command is run from.
BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """All configuration in one typed object, loaded from .env / environment."""

    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    app_name: str = "Disaster Response Coordination POC"

    # Database
    database_url: str = "postgresql+psycopg://disaster:disaster@localhost:5434/disaster_db"

    @field_validator("database_url")
    @classmethod
    def use_psycopg_driver(cls, url: str) -> str:
        """Hosted Postgres (Render, Neon, Supabase) gives postgres:// URLs; SQLAlchemy needs the psycopg 3 driver name."""
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url

    # LLM: "gemini" | "openai" | "none" (none = deterministic fallback only). Keys are only ever read here.
    llm_provider: str = "none"
    llm_api_key: str | None = Field(default=None, repr=False)     # repr=False: never shown in logs/tracebacks
    gemini_api_key: str | None = Field(default=None, repr=False)  # used when llm_provider == "gemini"
    llm_model: str | None = None
    llm_timeout_s: float = 10.0

    @property
    def active_llm_key(self) -> str | None:
        """The key for the selected provider (Gemini prefers GEMINI_API_KEY, then LLM_API_KEY)."""
        if self.llm_provider == "gemini":
            return self.gemini_api_key or self.llm_api_key
        if self.llm_provider == "openai":
            return self.llm_api_key
        return None

    # External integrations
    weather_mode: str = "live"          # live | simulation
    routing_mode: str = "live"          # live | simulation
    open_meteo_url: str = "https://api.open-meteo.com/v1/forecast"
    osrm_url: str = "https://router.project-osrm.org"
    http_timeout_s: float = 5.0
    weather_cache_min: int = 10
    # Nominatim is OFF by default: the demo district is fictional, so real-world lookups would
    # return wrong places. Enable only with real addresses (policy: 1 req/s, identifying User-Agent).
    geocoding_nominatim: bool = False
    nominatim_url: str = "https://nominatim.openstreetmap.org/search"
    nominatim_user_agent: str = "disaster-response-poc/0.1 (decision-support prototype)"

    # Deterministic thresholds (documented, not AI-decided)
    shelter_alert_threshold: float = 0.85
    duplicate_distance_m: float = 500
    duplicate_time_window_min: int = 60

    # CORS_ORIGINS in .env: "*" = any origin (default), or a comma-separated list
    # (http://localhost:5173,https://my-app.vercel.app), or a JSON list. NoDecode: we parse it ourselves,
    # so a plain "*" or comma list works (pydantic-settings would otherwise require JSON for a list).
    cors_origins: Annotated[list[str], NoDecode] = ["*"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                return json.loads(text)
            origins = [o.strip().rstrip("/") for o in text.split(",") if o.strip()]
            return origins or ["*"]  # empty value = not configured = allow all
        return value

    # Services
    reports_dir: Path = BACKEND_DIR / "reports"   # generated PDFs (git-ignored)
    overdue_check_s: int = 60                      # background check for actions past due_by
    default_actor: str = "coordinator"             # used when no X-User header is sent (demo only)


@lru_cache
def get_settings() -> Settings:
    return Settings()
