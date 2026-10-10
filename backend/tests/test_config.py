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
