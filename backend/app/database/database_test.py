import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

import app.database.database as db_module
from app.blocks.models import Block
from app.database.database import (
    _DATABASE_URL,
    _int_from_env,
    _require_database_url,
    engine,
    normalize_database_url,
)


def test_database_url_is_configured():
    assert _DATABASE_URL != ""


def test_engine_targets_postgresql():
    assert engine.dialect.name == "postgresql"
    assert engine.dialect.driver == "psycopg"


def test_session_local_returns_session():
    with db_module.SessionLocal() as session:
        assert isinstance(session, Session)


def test_session_can_execute_query():
    with db_module.SessionLocal() as session:
        result = session.execute(text("SELECT 1"))
        row = result.fetchone()
        assert row[0] == 1


def test_base_has_metadata():
    assert db_module.Base.metadata is not None


# ─── DATABASE_URL is mandatory ───────────────────────────────────────────────


def test_require_database_url_returns_the_normalized_value(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@db:5432/example_db")
    assert _require_database_url() == "postgresql+psycopg://user:pw@db:5432/example_db"


@pytest.mark.parametrize("value", ["", "   "])
def test_require_database_url_refuses_an_empty_value(monkeypatch, value):
    monkeypatch.setenv("DATABASE_URL", value)
    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        _require_database_url()


def test_require_database_url_refuses_a_missing_value(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"):
        _require_database_url()


# ─── Pool settings ───────────────────────────────────────────────────────────


def test_pool_is_sized_for_the_worker_threads():
    """
    Defaults match AnyIO's 40 worker threads: 10 kept open, 30 on demand.
    The test environment sets neither variable, so the defaults apply.
    """
    assert engine.pool.size() == 10
    assert engine.pool._max_overflow == 30


def test_pool_capacity_counts_kept_and_overflow_connections():
    from app.database.database import POOL_CAPACITY

    assert POOL_CAPACITY == engine.pool.size() + engine.pool._max_overflow


def test_pool_checks_connections_before_use():
    assert engine.pool._pre_ping is True


def test_int_from_env_uses_the_default_when_unset(monkeypatch):
    monkeypatch.delenv("EXAMPLE_SETTING", raising=False)
    assert _int_from_env("EXAMPLE_SETTING", default=7, minimum=0) == 7


def test_int_from_env_uses_the_default_when_blank(monkeypatch):
    monkeypatch.setenv("EXAMPLE_SETTING", "  ")
    assert _int_from_env("EXAMPLE_SETTING", default=7, minimum=0) == 7


def test_int_from_env_reads_a_value(monkeypatch):
    monkeypatch.setenv("EXAMPLE_SETTING", "25")
    assert _int_from_env("EXAMPLE_SETTING", default=7, minimum=0) == 25


def test_int_from_env_rejects_a_non_integer(monkeypatch):
    monkeypatch.setenv("EXAMPLE_SETTING", "many")
    with pytest.raises(RuntimeError, match="must be an integer"):
        _int_from_env("EXAMPLE_SETTING", default=7, minimum=0)


def test_int_from_env_rejects_a_value_below_the_minimum(monkeypatch):
    monkeypatch.setenv("EXAMPLE_SETTING", "0")
    with pytest.raises(RuntimeError, match="at least 1"):
        _int_from_env("EXAMPLE_SETTING", default=7, minimum=1)


# ─── Models and migrations agree ─────────────────────────────────────────────


def test_models_match_the_migrated_schema(test_engine):
    """
    The models must describe exactly the schema the migrations build.

    ``alembic revision --autogenerate`` compares the two. Any difference here
    would turn into a migration that rebuilds production tables towards the
    models, for example converting JSONB columns back to JSON or dropping
    indexes that only the migrations declare. A failure lists the differences
    to resolve, either in the models or with a new migration.
    """
    with test_engine.connect() as connection:
        context = MigrationContext.configure(connection)
        differences = compare_metadata(context, Block.metadata)
    assert differences == []


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
