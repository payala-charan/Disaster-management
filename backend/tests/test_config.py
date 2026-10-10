import pytest

from app.core.config import Settings


@pytest.mark.parametrize("given, expected", [
    ("postgres://u:p@host:5432/db", "postgresql+psycopg://u:p@host:5432/db"),
    ("postgresql://u:p@host/db?sslmode=require", "postgresql+psycopg://u:p@host/db?sslmode=require"),
    ("postgresql+psycopg://u:p@localhost:5434/db", "postgresql+psycopg://u:p@localhost:5434/db"),
])
def test_database_url_gets_psycopg_driver(given, expected):
    assert Settings(_env_file=None, database_url=given).database_url == expected


def test_api_key_never_in_repr():
    assert "sk-secret" not in repr(Settings(_env_file=None, llm_api_key="sk-secret"))


@pytest.mark.parametrize("given, expected", [
    ("*", ["*"]),
    ("", ["*"]),                                                             # unset/empty -> allow all
    ("http://localhost:5173", ["http://localhost:5173"]),
    ("http://localhost:5173, https://my-app.vercel.app/", ["http://localhost:5173", "https://my-app.vercel.app"]),
    ('["http://localhost:5173","https://my-app.vercel.app"]', ["http://localhost:5173", "https://my-app.vercel.app"]),
])
def test_cors_origins_from_env(monkeypatch, given, expected):
    monkeypatch.setenv("CORS_ORIGINS", given)
    assert Settings(_env_file=None).cors_origins == expected


def test_cors_origins_default_is_all():
    assert Settings(_env_file=None).cors_origins == ["*"]
