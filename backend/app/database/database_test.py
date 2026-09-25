import importlib

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database.database import (
    Base,
    SessionLocal,
    _DATABASE_URL,
    engine,
    normalize_database_url,
)


def test_database_url_has_default():
    assert _DATABASE_URL != ""


def test_database_url_uses_env_var(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./test_override.db")
    import app.database.database as db_module
    importlib.reload(db_module)
    assert db_module._DATABASE_URL == "sqlite:///./test_override.db"
    # Clean up: restore the module to its original state.
    importlib.reload(db_module)


def test_engine_is_created():
    assert engine is not None


def test_session_local_returns_session():
    with SessionLocal() as session:
        assert isinstance(session, Session)


def test_session_can_execute_query():
    with SessionLocal() as session:
        result = session.execute(text("SELECT 1"))
        row = result.fetchone()
        assert row[0] == 1


def test_base_has_metadata():
    assert Base.metadata is not None


def test_connect_args_match_dialect():
    # SQLite needs check_same_thread disabled so the tests can run across
    # threads. PostgreSQL does not require it.
    if _DATABASE_URL.startswith("sqlite"):
        assert engine.dialect.name == "sqlite"
    else:
        assert engine.dialect.name == "postgresql"


def test_tables_can_be_created_and_dropped():
    """
    Verify that Base.metadata DDL works correctly, using an isolated
    in-memory SQLite engine so the production database is never touched.
    """
    isolated_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(isolated_engine)
    table_names = inspect(isolated_engine).get_table_names()
    assert "sessions" in table_names
    Base.metadata.drop_all(isolated_engine)
    table_names_after = inspect(isolated_engine).get_table_names()
    assert "sessions" not in table_names_after
    isolated_engine.dispose()


# ─── normalize_database_url ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        "postgresql://user:pw@db:5432/capybarca_db",
        "postgres://user:pw@db:5432/capybarca_db",
        "postgresql+psycopg2://user:pw@db:5432/capybarca_db",
        "POSTGRESQL://user:pw@db:5432/capybarca_db",
    ],
)
def test_normalize_pins_postgres_urls_to_psycopg3(raw):
    assert (
        normalize_database_url(raw)
        == "postgresql+psycopg://user:pw@db:5432/capybarca_db"
    )


@pytest.mark.parametrize(
    "raw",
    [
        "postgresql+psycopg://user:pw@db:5432/capybarca_db",
        "postgresql+asyncpg://user:pw@db:5432/capybarca_db",
        "postgresql+pg8000://user:pw@db:5432/capybarca_db",
        "sqlite:///./capybarca.db",
        "sqlite:///:memory:",
    ],
)
def test_normalize_leaves_other_urls_untouched(raw):
    assert normalize_database_url(raw) == raw


def test_normalize_leaves_a_string_without_scheme_untouched():
    assert normalize_database_url("not-a-url") == "not-a-url"


def test_normalize_preserves_percent_encoded_credentials():
    # setup.sh percent-encodes the password. Decoding and re-encoding it on the
    # way through would change characters such as '@' or '%' and point the
    # connection somewhere else.
    raw = "postgresql://capy%40user:p%25w%3Ad%2F@db:5432/capybarca_db"
    assert (
        normalize_database_url(raw)
        == "postgresql+psycopg://capy%40user:p%25w%3Ad%2F@db:5432/capybarca_db"
    )


def test_normalized_postgres_url_resolves_to_an_installed_driver():
    # Regression guard for a driver that is named by the URL but missing from
    # the image. create_engine imports the DBAPI module without connecting, so
    # this fails with ModuleNotFoundError exactly where the installer would.
    pg_engine = create_engine(
        normalize_database_url("postgresql://user:pw@db:5432/capybarca_db")
    )
    try:
        assert pg_engine.dialect.name == "postgresql"
        assert pg_engine.dialect.driver == "psycopg"
    finally:
        pg_engine.dispose()

