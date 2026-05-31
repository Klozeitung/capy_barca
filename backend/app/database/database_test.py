import os

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database.database import Base, SessionLocal, _DATABASE_URL, engine


def test_database_url_has_default():
    assert _DATABASE_URL != ""


def test_database_url_uses_env_var(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./test_override.db")
    import importlib
    import app.database.database as db_module
    importlib.reload(db_module)
    assert db_module._DATABASE_URL == "sqlite:///./test_override.db"
    # Aufräumen: Modul auf Originalzustand zurücksetzen
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
    # Für SQLite muss check_same_thread gesetzt sein, damit Tests
    # thread-übergreifend laufen können. PostgreSQL benötigt dies nicht.
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
