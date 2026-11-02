"""
Tests for revision 0032, which adds ``alerts.kind`` and ``users.timezone``.

They run against the PostgreSQL test database, migrated to head, inside a
transaction that is always rolled back.
"""
import importlib.util
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "0032_calendar_alerts_timezone.py"
)
_spec = importlib.util.spec_from_file_location("migration_0032", _PATH)
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)


@pytest.fixture
def connection(test_engine):
    with test_engine.connect() as conn:
        transaction = conn.begin()
        try:
            yield conn
        finally:
            transaction.rollback()


def _run(connection, step) -> None:
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        step()


def _columns(connection, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(connection).get_columns(table)}


def _alert_scene(connection):
    user_id, database_id, entry_id, schema_id = (uuid.uuid4() for _ in range(4))
    connection.execute(sa.text(
        "INSERT INTO users (id, username, password_hash) VALUES (:id, 'person-a', 'x')"
    ), {"id": user_id})
    connection.execute(sa.text(
        "INSERT INTO blocks (id, type, position) VALUES (:id, 'database', 1.0)"
    ), {"id": database_id})
    connection.execute(sa.text(
        "INSERT INTO blocks (id, type, position, parent_id) VALUES (:id, 'page', 1.0, :p)"
    ), {"id": entry_id, "p": database_id})
    connection.execute(sa.text(
        "INSERT INTO property_schemas (id, database_id, name, type, position) "
        "VALUES (:id, :db, 'When', 'date', 1.0)"
    ), {"id": schema_id, "db": database_id})
    return user_id, entry_id, schema_id


def _insert_alert(connection, user_id, entry_id, schema_id, kind, fired=False):
    connection.execute(sa.text(
        "INSERT INTO alerts (id, block_id, schema_id, user_id, fire_at, kind, fired_at) "
        "VALUES (:id, :b, :s, :u, :t, :k, :f)"
    ), {"id": uuid.uuid4(), "b": entry_id, "s": schema_id, "u": user_id,
        "t": datetime(2045, 7, 17, tzinfo=timezone.utc), "k": kind,
        "f": datetime(2045, 7, 17, tzinfo=timezone.utc) if fired else None})


def test_revision_follows_the_alerts_revision():
    assert migration.revision == "f2c4b3d50032"
    assert migration.down_revision == "e1b3a2c40031"


def test_columns_exist_and_alerts_default_to_user(connection):
    assert migration.KIND_COLUMN in _columns(connection, "alerts")
    assert migration.TIMEZONE_COLUMN in _columns(connection, "users")
    user_id, entry_id, schema_id = _alert_scene(connection)
    connection.execute(sa.text(
        "INSERT INTO alerts (id, block_id, schema_id, user_id, fire_at) VALUES (:id, :b, :s, :u, now())"
    ), {"id": uuid.uuid4(), "b": entry_id, "s": schema_id, "u": user_id})
    assert connection.scalar(sa.text("SELECT kind FROM alerts")) == "user"


def test_one_unfired_system_alert_per_entry_property_and_recipient(connection):
    user_id, entry_id, schema_id = _alert_scene(connection)
    _insert_alert(connection, user_id, entry_id, schema_id, "system", fired=True)
    _insert_alert(connection, user_id, entry_id, schema_id, "system")
    _insert_alert(connection, user_id, entry_id, schema_id, "user")
    _insert_alert(connection, user_id, entry_id, schema_id, "user")
    with pytest.raises(sa.exc.IntegrityError):
        with connection.begin_nested():
            _insert_alert(connection, user_id, entry_id, schema_id, "system")


def test_an_unknown_kind_is_refused(connection):
    user_id, entry_id, schema_id = _alert_scene(connection)
    with pytest.raises(sa.exc.IntegrityError):
        with connection.begin_nested():
            _insert_alert(connection, user_id, entry_id, schema_id, "other")


def test_upgrade_gives_existing_accounts_berlin(connection):
    _run(connection, migration.downgrade)
    connection.execute(sa.text(
        "INSERT INTO users (id, username, password_hash) VALUES (:id, 'person-b', 'x')"
    ), {"id": uuid.uuid4()})

    _run(connection, migration.upgrade)

    assert connection.scalar(sa.text("SELECT timezone FROM users")) == "Europe/Berlin"


def test_downgrade_removes_system_alerts_and_both_columns(connection):
    user_id, entry_id, schema_id = _alert_scene(connection)
    _insert_alert(connection, user_id, entry_id, schema_id, "system")
    _insert_alert(connection, user_id, entry_id, schema_id, "user")

    _run(connection, migration.downgrade)

    assert migration.KIND_COLUMN not in _columns(connection, "alerts")
    assert migration.TIMEZONE_COLUMN not in _columns(connection, "users")
    assert connection.scalar(sa.text("SELECT count(*) FROM alerts")) == 1
