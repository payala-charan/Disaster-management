"""Suite-wide fixtures.

- Tests must never call the real OpenAI API (guard below).
- PostGIS tests run against a separate `disaster_test` database on the same server, so running the
  tests never wipes the demo data in `disaster_db`. If the container is not running, those tests are
  skipped (not failed) so the pure-Python tests stay usable on their own.
"""

from collections.abc import Iterator

import openai
import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db import models  # noqa: F401  (registers tables)
from app.db.seed import load_seed, write_seed
from app.db.session import Base

TEST_DB = "disaster_test"


class _NoRealOpenAI:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("Tests must not create a real OpenAI client: inject a fake client instead")


@pytest.fixture(autouse=True)
def block_real_openai(monkeypatch):
    monkeypatch.setattr(openai, "OpenAI", _NoRealOpenAI)


@pytest.fixture(scope="session")
def test_engine() -> Iterator[Engine]:
    url = make_url(get_settings().database_url)
    admin = create_engine(url, isolation_level="AUTOCOMMIT")  # CREATE DATABASE can't run in a transaction
    try:
        with admin.connect() as conn:
            exists = conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": TEST_DB})
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
    except OperationalError:
        pytest.skip("PostGIS not reachable: run `docker compose up -d`")
    finally:
        admin.dispose()

    engine = create_engine(url.set(database=TEST_DB))
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(test_engine: Engine):
    """A freshly seeded test database; returns a session factory bound to it."""
    with test_engine.begin() as conn:
        tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    factory = sessionmaker(bind=test_engine, expire_on_commit=False)
    with factory() as s:
        write_seed(s, load_seed())
    return factory


@pytest.fixture
def db(session_factory) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
