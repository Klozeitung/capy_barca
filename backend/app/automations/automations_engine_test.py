"""
Tests for the automation engine.

Pure unit tests cover the stateless helper functions (_matches, _render,
_render_body, _matches_actor_filter, _matches_trigger).  Integration tests use
the in-memory SQLite database provided by the autouse ``isolated_db`` fixture
in conftest.py, and exercise the bulk action handler (_handle_bulk_upsert_value),
which delegates filtering to the shared repository query evaluator.

Async engine functions are exercised via ``asyncio.run()`` — no external
test runner plugin is required.
"""
import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.automations.automations_engine import (
    TriggerEvent,
    _build_context,
    _handle_bulk_upsert_value,
    _matches,
    _matches_actor_filter,
    _matches_trigger,
    _render,
    _render_body,
    receive,
)


# ─── TriggerEvent ─────────────────────────────────────────────────────────────


def test_trigger_event_defaults():
    ev = TriggerEvent(
        action_type="PropertyUpdate",
        origin="user",
        actor_uuid="u1",
        db_uuid="db1",
        property_uuid="p1",
        old_value="",
        new_value="",
    )
    assert ev.entry_id == ""


# ─── _matches: wildcard ───────────────────────────────────────────────────────


def test_matches_all_wildcards():
    trigger = {
        "action_type": "", "origin": "", "actor_uuid": "",
        "db_uuid": "", "property_uuid": "", "old_value": "", "new_value": "",
    }
    ev = TriggerEvent(
        action_type="PropertyUpdate", origin="user", actor_uuid="some-user",
        db_uuid="some-db", property_uuid="some-prop", old_value="x", new_value="y",
    )
    assert _matches(trigger, ev) is True


def test_matches_partial_wildcards():
    trigger = {
        "action_type": "PropertyUpdate",
        "origin": "user",
        "actor_uuid": "",
        "db_uuid": "db-X",
        "property_uuid": "",
        "old_value": "",
        "new_value": "",
    }
    ev = TriggerEvent(
        action_type="PropertyUpdate", origin="user", actor_uuid="anyone",
        db_uuid="db-X", property_uuid="any-prop", old_value="", new_value="",
    )
    assert _matches(trigger, ev) is True


# ─── _matches: exact ──────────────────────────────────────────────────────────


def test_matches_exact_hit():
    trigger = {
        "action_type": "PropertyUpdate", "origin": "user",
        "actor_uuid": "", "db_uuid": "db-1", "property_uuid": "p-1",
        "old_value": "", "new_value": "",
    }
    ev = TriggerEvent(
        action_type="PropertyUpdate", origin="user", actor_uuid="u",
        db_uuid="db-1", property_uuid="p-1", old_value="", new_value="",
    )
    assert _matches(trigger, ev) is True


def test_matches_exact_miss_action_type():
    trigger = {"action_type": "EntryCreated", "origin": "", "actor_uuid": "",
               "db_uuid": "", "property_uuid": "", "old_value": "", "new_value": ""}
    ev = TriggerEvent(
        action_type="PropertyUpdate", origin="user", actor_uuid="",
        db_uuid="", property_uuid="", old_value="", new_value="",
    )
    assert _matches(trigger, ev) is False


def test_matches_exact_miss_db_uuid():
    trigger = {"action_type": "", "origin": "", "actor_uuid": "",
               "db_uuid": "db-A", "property_uuid": "", "old_value": "", "new_value": ""}
    ev = TriggerEvent(
        action_type="PropertyUpdate", origin="user", actor_uuid="",
        db_uuid="db-B", property_uuid="", old_value="", new_value="",
    )
    assert _matches(trigger, ev) is False


# ─── _matches: negation ───────────────────────────────────────────────────────


def test_matches_negation_excludes_matching_value():
    trigger = {"action_type": "", "origin": "", "actor_uuid": "!user-X",
               "db_uuid": "", "property_uuid": "", "old_value": "", "new_value": ""}
    ev = TriggerEvent(
        action_type="PropertyUpdate", origin="user", actor_uuid="user-X",
        db_uuid="db", property_uuid="p", old_value="", new_value="",
    )
    assert _matches(trigger, ev) is False


def test_matches_negation_passes_different_value():
    trigger = {"action_type": "", "origin": "", "actor_uuid": "!user-X",
               "db_uuid": "", "property_uuid": "", "old_value": "", "new_value": ""}
    ev = TriggerEvent(
        action_type="PropertyUpdate", origin="user", actor_uuid="user-Y",
        db_uuid="db", property_uuid="p", old_value="", new_value="",
    )
    assert _matches(trigger, ev) is True


def test_matches_negation_empty_stored_is_wildcard_not_negation():
    """A stored value of '' is a wildcard; '!' would be negation of empty string."""
    trigger = {"action_type": "", "origin": "", "actor_uuid": "",
               "db_uuid": "", "property_uuid": "", "old_value": "", "new_value": ""}
    ev = TriggerEvent(
        action_type="X", origin="user", actor_uuid="",
        db_uuid="", property_uuid="", old_value="", new_value="",
    )
    assert _matches(trigger, ev) is True


# ─── _matches_actor_filter ────────────────────────────────────────────────────


def test_actor_filter_empty_entries_passes_all():
    """An empty entries list with mode=specific passes any actor."""
    af = {"mode": "specific", "entries": [], "include_automation": False}
    assert _matches_actor_filter(af, "any-user") is True


def test_actor_filter_positive_matching_actor_passes():
    af = {
        "mode": "specific",
        "entries": [{"uuid": "user-A", "state": "positive"}],
        "include_automation": False,
    }
    assert _matches_actor_filter(af, "user-A") is True


def test_actor_filter_positive_non_matching_actor_fails():
    af = {
        "mode": "specific",
        "entries": [{"uuid": "user-A", "state": "positive"}],
        "include_automation": False,
    }
    assert _matches_actor_filter(af, "user-B") is False


def test_actor_filter_negative_matching_actor_fails():
    af = {
        "mode": "specific",
        "entries": [{"uuid": "user-X", "state": "negative"}],
        "include_automation": False,
    }
    assert _matches_actor_filter(af, "user-X") is False


def test_actor_filter_negative_non_matching_actor_passes():
    af = {
        "mode": "specific",
        "entries": [{"uuid": "user-X", "state": "negative"}],
        "include_automation": False,
    }
    assert _matches_actor_filter(af, "user-Y") is True


def test_actor_filter_positive_and_negative_combined_pass():
    """Actor is in positive list and not in negative list -> passes."""
    af = {
        "mode": "specific",
        "entries": [
            {"uuid": "user-A", "state": "positive"},
            {"uuid": "user-B", "state": "negative"},
        ],
        "include_automation": False,
    }
    assert _matches_actor_filter(af, "user-A") is True


def test_actor_filter_positive_and_negative_combined_fail_by_positive():
    """Actor is not in positive list -> fails regardless of negative list."""
    af = {
        "mode": "specific",
        "entries": [
            {"uuid": "user-A", "state": "positive"},
            {"uuid": "user-B", "state": "negative"},
        ],
        "include_automation": False,
    }
    assert _matches_actor_filter(af, "user-C") is False


def test_actor_filter_positive_and_negative_combined_fail_by_negative():
    """Actor is in both positive and negative -> deny-list wins."""
    af = {
        "mode": "specific",
        "entries": [
            {"uuid": "user-A", "state": "positive"},
            {"uuid": "user-A", "state": "negative"},
        ],
        "include_automation": False,
    }
    assert _matches_actor_filter(af, "user-A") is False


def test_actor_filter_only_negative_unknown_actor_passes():
    """Only negative entries defined; an unlisted actor passes."""
    af = {
        "mode": "specific",
        "entries": [{"uuid": "user-X", "state": "negative"}],
        "include_automation": False,
    }
    assert _matches_actor_filter(af, "user-unknown") is True


# ─── _matches: actor_filter integration ──────────────────────────────────────


def test_matches_with_actor_filter_mode_any_is_noop():
    """mode=any must not restrict matching beyond the base actor_uuid check."""
    trigger = {
        "action_type": "", "origin": "", "actor_uuid": "",
        "db_uuid": "", "property_uuid": "", "old_value": "", "new_value": "",
        "actor_filter": {"mode": "any", "entries": [], "include_automation": False},
    }
    ev = TriggerEvent(
        action_type="PropertyUpdate", origin="user", actor_uuid="any-user",
        db_uuid="db", property_uuid="p", old_value="", new_value="",
    )
    assert _matches(trigger, ev) is True


def test_matches_with_actor_filter_specific_passes():
    trigger = {
        "action_type": "", "origin": "", "actor_uuid": "",
        "db_uuid": "", "property_uuid": "", "old_value": "", "new_value": "",
        "actor_filter": {
            "mode": "specific",
            "entries": [{"uuid": "user-A", "state": "positive"}],
            "include_automation": False,
        },
    }
    ev = TriggerEvent(
        action_type="PropertyUpdate", origin="user", actor_uuid="user-A",
        db_uuid="db", property_uuid="p", old_value="", new_value="",
    )
    assert _matches(trigger, ev) is True


def test_matches_with_actor_filter_specific_fails():
    trigger = {
        "action_type": "", "origin": "", "actor_uuid": "",
        "db_uuid": "", "property_uuid": "", "old_value": "", "new_value": "",
        "actor_filter": {
            "mode": "specific",
            "entries": [{"uuid": "user-A", "state": "positive"}],
            "include_automation": False,
        },
    }
    ev = TriggerEvent(
        action_type="PropertyUpdate", origin="user", actor_uuid="user-B",
        db_uuid="db", property_uuid="p", old_value="", new_value="",
    )
    assert _matches(trigger, ev) is False


def test_matches_without_actor_filter_backward_compat():
    """Triggers saved before actor_filter existed must still match correctly."""
    trigger = {
        "action_type": "PropertyUpdate", "origin": "user", "actor_uuid": "",
        "db_uuid": "db-1", "property_uuid": "", "old_value": "", "new_value": "",
    }
    ev = TriggerEvent(
        action_type="PropertyUpdate", origin="user", actor_uuid="any-user",
        db_uuid="db-1", property_uuid="p", old_value="", new_value="",
    )
    assert _matches(trigger, ev) is True


# ─── _matches_trigger: multi-trigger (new array format) ──────────────────────


def _make_ev(**overrides) -> TriggerEvent:
    defaults = dict(
        action_type="PropertyUpdate", origin="user", actor_uuid="u",
        db_uuid="db-1", property_uuid="p-1", old_value="", new_value="",
    )
    defaults.update(overrides)
    return TriggerEvent(**defaults)


def test_matches_trigger_single_dict_backward_compat():
    """Legacy single-dict trigger field is handled transparently."""
    trigger = {
        "action_type": "PropertyUpdate", "origin": "user", "actor_uuid": "",
        "db_uuid": "db-1", "property_uuid": "", "old_value": "", "new_value": "",
    }
    assert _matches_trigger(trigger, _make_ev()) is True


def test_matches_trigger_single_dict_miss():
    """Legacy single-dict that does not match the event."""
    trigger = {
        "action_type": "PropertyUpdate", "origin": "user", "actor_uuid": "",
        "db_uuid": "db-OTHER", "property_uuid": "", "old_value": "", "new_value": "",
    }
    assert _matches_trigger(trigger, _make_ev(db_uuid="db-1")) is False


def test_matches_trigger_list_all_match():
    """All triggers in the list match — returns True."""
    triggers = [
        {"action_type": "", "origin": "", "actor_uuid": "", "db_uuid": "db-1",
         "property_uuid": "", "old_value": "", "new_value": ""},
        {"action_type": "", "origin": "", "actor_uuid": "", "db_uuid": "db-1",
         "property_uuid": "", "old_value": "", "new_value": ""},
    ]
    assert _matches_trigger(triggers, _make_ev()) is True


def test_matches_trigger_list_first_matches():
    """First trigger matches, second does not — OR logic returns True."""
    triggers = [
        {"action_type": "PropertyUpdate", "origin": "", "actor_uuid": "",
         "db_uuid": "db-1", "property_uuid": "", "old_value": "", "new_value": ""},
        {"action_type": "PropertyUpdate", "origin": "", "actor_uuid": "",
         "db_uuid": "db-OTHER", "property_uuid": "", "old_value": "", "new_value": ""},
    ]
    assert _matches_trigger(triggers, _make_ev(db_uuid="db-1")) is True


def test_matches_trigger_list_second_matches():
    """Second trigger matches, first does not — OR logic returns True."""
    triggers = [
        {"action_type": "PropertyUpdate", "origin": "", "actor_uuid": "",
         "db_uuid": "db-OTHER", "property_uuid": "", "old_value": "", "new_value": ""},
        {"action_type": "PropertyUpdate", "origin": "", "actor_uuid": "",
         "db_uuid": "db-1", "property_uuid": "", "old_value": "", "new_value": ""},
    ]
    assert _matches_trigger(triggers, _make_ev(db_uuid="db-1")) is True


def test_matches_trigger_list_none_match():
    """No trigger in the list matches — returns False."""
    triggers = [
        {"action_type": "PropertyUpdate", "origin": "", "actor_uuid": "",
         "db_uuid": "db-A", "property_uuid": "", "old_value": "", "new_value": ""},
        {"action_type": "PropertyUpdate", "origin": "", "actor_uuid": "",
         "db_uuid": "db-B", "property_uuid": "", "old_value": "", "new_value": ""},
    ]
    assert _matches_trigger(triggers, _make_ev(db_uuid="db-1")) is False


def test_matches_trigger_empty_list_returns_false():
    """An empty trigger list never matches."""
    assert _matches_trigger([], _make_ev()) is False


def test_matches_trigger_invalid_type_returns_false():
    """Non-dict, non-list trigger field returns False without raising."""
    assert _matches_trigger(None, _make_ev()) is False
    assert _matches_trigger(42, _make_ev()) is False


# ─── _render ──────────────────────────────────────────────────────────────────


def test_render_replaces_known_variable():
    ctx = {"trigger.entry_id": "entry-123"}
    result = _render("/api/entries/{trigger.entry_id}/values", ctx)
    assert result == "/api/entries/entry-123/values"


def test_render_leaves_unknown_placeholders_intact():
    ctx = {"trigger.entry_id": "e1"}
    result = _render("/api/{unknown}/foo", ctx)
    assert result == "/api/{unknown}/foo"


def test_render_today():
    from datetime import date
    ctx = {"today()": date.today().isoformat()}
    result = _render("{today()}", ctx)
    assert result == date.today().isoformat()


# ─── _render_body ─────────────────────────────────────────────────────────────


def test_render_body_flat_dict():
    ctx = {"trigger.new_value": "Done"}
    body = {"value": {"option": "{trigger.new_value}"}}
    result = _render_body(body, ctx)
    assert result == {"value": {"option": "Done"}}


def test_render_body_nested():
    ctx = {"trigger.db_uuid": "db-99", "trigger.entry_id": "e-1"}
    body = {
        "endpoint": "PUT /api/databases/{trigger.db_uuid}/entries/{trigger.entry_id}",
        "meta": {"db": "{trigger.db_uuid}"},
    }
    result = _render_body(body, ctx)
    assert result["endpoint"] == "PUT /api/databases/db-99/entries/e-1"
    assert result["meta"]["db"] == "db-99"


def test_render_body_list():
    ctx = {"trigger.entry_id": "e-42"}
    body = ["{trigger.entry_id}", "static"]
    result = _render_body(body, ctx)
    assert result == ["e-42", "static"]


def test_render_body_non_string_passthrough():
    ctx = {"trigger.entry_id": "e-1"}
    body = {"count": 42, "flag": True, "nothing": None}
    result = _render_body(body, ctx)
    assert result == {"count": 42, "flag": True, "nothing": None}


# ─── _build_context ───────────────────────────────────────────────────────────


def test_build_context_keys():
    ev = TriggerEvent(
        action_type="PropertyUpdate", origin="user", actor_uuid="u",
        db_uuid="db-1", property_uuid="p-1", old_value="old", new_value="new",
        entry_id="e-1",
    )
    ctx = _build_context(ev)
    assert ctx["trigger.entry_id"] == "e-1"
    assert ctx["trigger.db_uuid"] == "db-1"
    assert ctx["trigger.property_uuid"] == "p-1"
    assert ctx["trigger.new_value"] == "new"
    assert "today()" in ctx


# ─── receive: origin guard ────────────────────────────────────────────────────


def test_receive_ignores_non_user_origin():
    """Events with origin != 'user' must be silently dropped."""
    import app.database.database as db_module

    ev = TriggerEvent(
        action_type="PropertyUpdate", origin="automation",
        actor_uuid="auto-1", db_uuid=str(uuid.uuid4()),
        property_uuid="p", old_value="", new_value="", entry_id="e",
    )
    with db_module.SessionLocal() as db:
        with patch(
            "app.automations.automations_engine._query"
        ) as mock_query:
            asyncio.run(receive(ev, db))
            mock_query.assert_not_called()


# ─── receive: no matching automations ────────────────────────────────────────


def test_receive_no_automations_is_noop():
    """When no automations exist the engine completes without error."""
    import app.database.database as db_module

    ev = TriggerEvent(
        action_type="PropertyUpdate", origin="user",
        actor_uuid="u", db_uuid=str(uuid.uuid4()),
        property_uuid="p", old_value="", new_value="", entry_id="e",
    )
    with db_module.SessionLocal() as db:
        asyncio.run(receive(ev, db))  # must not raise


# ─── receive: matching automation fires execution ────────────────────────────


def test_receive_fires_execute_for_matching_automation():
    """When a matching automation exists, _execute must be called once."""
    import app.database.database as db_module
    from app.automations.automations_repository import create_automation

    db_id = uuid.uuid4()

    trigger = {
        "action_type": "PropertyUpdate",
        "origin": "user",
        "actor_uuid": "",
        "db_uuid": str(db_id),
        "property_uuid": "",
        "old_value": "",
        "new_value": "",
    }

    with db_module.SessionLocal() as db:
        auto = create_automation(
            db,
            database_id=db_id,
            name="Test",
            trigger=trigger,
            actions=[],
        )
        db.commit()

        ev = TriggerEvent(
            action_type="PropertyUpdate", origin="user",
            actor_uuid="u", db_uuid=str(db_id),
            property_uuid="p", old_value="", new_value="", entry_id="e",
        )

        with patch(
            "app.automations.automations_engine._execute",
            new_callable=AsyncMock,
        ) as mock_exec:
            asyncio.run(receive(ev, db))
            mock_exec.assert_called_once()
            called_auto, called_ev, called_db = mock_exec.call_args[0]
            assert called_auto.id == auto.id
            assert called_ev is ev


def test_receive_fires_execute_for_multi_trigger_automation():
    """Multi-trigger automation (list format) fires when any trigger matches."""
    import app.database.database as db_module
    from app.automations.automations_repository import create_automation

    db_id   = uuid.uuid4()
    other_db = uuid.uuid4()

    # Two triggers: one matching the test event, one for a different DB.
    triggers = [
        {
            "action_type": "PropertyUpdate", "origin": "user", "actor_uuid": "",
            "db_uuid": str(other_db), "property_uuid": "",
            "old_value": "", "new_value": "",
        },
        {
            "action_type": "PropertyUpdate", "origin": "user", "actor_uuid": "",
            "db_uuid": str(db_id), "property_uuid": "",
            "old_value": "", "new_value": "",
        },
    ]

    with db_module.SessionLocal() as db:
        auto = create_automation(
            db,
            database_id=db_id,
            name="MultiTrigger",
            trigger=triggers,
            actions=[],
        )
        db.commit()

        ev = TriggerEvent(
            action_type="PropertyUpdate", origin="user",
            actor_uuid="u", db_uuid=str(db_id),
            property_uuid="p", old_value="", new_value="", entry_id="e",
        )

        with patch(
            "app.automations.automations_engine._execute",
            new_callable=AsyncMock,
        ) as mock_exec:
            asyncio.run(receive(ev, db))
            mock_exec.assert_called_once()
            called_auto, _, _ = mock_exec.call_args[0]
            assert called_auto.id == auto.id


def test_receive_actor_filter_blocks_excluded_actor():
    """An automation with a specific actor filter must not fire for excluded actors."""
    import app.database.database as db_module
    from app.automations.automations_repository import create_automation

    db_id = uuid.uuid4()
    allowed_user = str(uuid.uuid4())
    blocked_user = str(uuid.uuid4())

    trigger = {
        "action_type": "PropertyUpdate",
        "origin": "user",
        "actor_uuid": "",
        "db_uuid": str(db_id),
        "property_uuid": "",
        "old_value": "",
        "new_value": "",
        "actor_filter": {
            "mode": "specific",
            "entries": [{"uuid": allowed_user, "state": "positive"}],
            "include_automation": False,
        },
    }

    with db_module.SessionLocal() as db:
        create_automation(
            db, database_id=db_id, name="Filtered", trigger=trigger, actions=[],
        )
        db.commit()

        ev = TriggerEvent(
            action_type="PropertyUpdate", origin="user",
            actor_uuid=blocked_user, db_uuid=str(db_id),
            property_uuid="p", old_value="", new_value="", entry_id="e",
        )

        with patch(
            "app.automations.automations_engine._execute",
            new_callable=AsyncMock,
        ) as mock_exec:
            asyncio.run(receive(ev, db))
            mock_exec.assert_not_called()


def test_receive_actor_filter_allows_matching_actor():
    """An automation with a positive actor filter must fire for the allowed actor."""
    import app.database.database as db_module
    from app.automations.automations_repository import create_automation

    db_id = uuid.uuid4()
    allowed_user = str(uuid.uuid4())

    trigger = {
        "action_type": "PropertyUpdate",
        "origin": "user",
        "actor_uuid": "",
        "db_uuid": str(db_id),
        "property_uuid": "",
        "old_value": "",
        "new_value": "",
        "actor_filter": {
            "mode": "specific",
            "entries": [{"uuid": allowed_user, "state": "positive"}],
            "include_automation": False,
        },
    }

    with db_module.SessionLocal() as db:
        auto = create_automation(
            db, database_id=db_id, name="Filtered", trigger=trigger, actions=[],
        )
        db.commit()

        ev = TriggerEvent(
            action_type="PropertyUpdate", origin="user",
            actor_uuid=allowed_user, db_uuid=str(db_id),
            property_uuid="p", old_value="", new_value="", entry_id="e",
        )

        with patch(
            "app.automations.automations_engine._execute",
            new_callable=AsyncMock,
        ) as mock_exec:
            asyncio.run(receive(ev, db))
            mock_exec.assert_called_once()
            called_auto, _, _ = mock_exec.call_args[0]
            assert called_auto.id == auto.id


# ─── _handle_bulk_upsert_value: setup helpers ─────────────────────────────────


def _seed_database(db):
    """Create a workspace root and an empty database block; return the database."""
    from app.blocks.models import WORKSPACE_ROOT_ID, Block
    from app.blocks import repository as repo

    if db.get(Block, WORKSPACE_ROOT_ID) is None:
        db.add(Block(id=WORKSPACE_ROOT_ID, type="workspace", position=0.0))
        db.commit()
    database = repo.create_block(
        db, type="database", position=1.0, parent_id=WORKSPACE_ROOT_ID,
    )
    db.commit()
    return database


def _make_entry(db, database, title=None, position=1.0, type="page"):
    from app.blocks import repository as repo

    entry = repo.create_block(
        db, type=type, position=position, parent_id=database.id,
        content={"title": title} if title else None,
    )
    db.commit()
    return entry


def _run_bulk(db, db_uuid, schema_id, filter_spec, body):
    """
    Run the bulk handler with the WS broadcast and computed hooks patched out,
    so the test isolates the entry-selection + upsert behaviour.
    """
    with patch(
        "app.ws.broadcaster.broadcast_block_event", new_callable=AsyncMock,
    ), patch(
        "app.blocks.computed.compute_all_for_entry",
    ), patch(
        "app.blocks.computed.compute_same_db_rollup_dependents",
    ):
        asyncio.run(_handle_bulk_upsert_value(
            db=db, db_uuid=db_uuid, schema_id=schema_id,
            filter_spec=filter_spec, body=body,
        ))


_DONE = {"value": {"text": "DONE"}}


def _value_text(db, page_id, schema_id):
    from app.blocks import repository as repo

    pv = repo.get_value(db, page_id, schema_id)
    return None if pv is None else pv.value


# ─── _handle_bulk_upsert_value: 'where' text filter ───────────────────────────


def test_bulk_where_text_contains_updates_only_matching():
    """A 'where' contains filter on a text property updates only matching entries."""
    import app.database.database as db_module
    from app.blocks import repository as repo

    with db_module.SessionLocal() as db:
        database = _seed_database(db)
        status = repo.create_schema(
            db, database_id=database.id, name="Status", type="text", position=1.0,
        )
        target = repo.create_schema(
            db, database_id=database.id, name="Result", type="text", position=2.0,
        )
        db.commit()
        e_hit  = _make_entry(db, database, "Hit",  position=1.0)
        e_miss = _make_entry(db, database, "Miss", position=2.0)
        repo.upsert_value(db, page_id=e_hit.id,  schema_id=status.id, value={"text": "open"})
        repo.upsert_value(db, page_id=e_miss.id, schema_id=status.id, value={"text": "closed"})
        db.commit()

        spec = {
            "mode": "where",
            "groups": [{
                "conjunction": "and",
                "filters": [{"schemaId": str(status.id), "operator": "contains", "value": "open"}],
            }],
        }
        _run_bulk(db, str(database.id), str(target.id), spec, _DONE)

        assert _value_text(db, e_hit.id,  target.id) == {"text": "DONE"}
        assert _value_text(db, e_miss.id, target.id) is None


# ─── _handle_bulk_upsert_value: 'where' name filter (issue #6) ─────────────────


def test_bulk_where_name_filter_updates_only_matching():
    """The name column ('__name__') is filterable in automation bulk actions."""
    import app.database.database as db_module
    from app.blocks import repository as repo

    with db_module.SessionLocal() as db:
        database = _seed_database(db)
        target = repo.create_schema(
            db, database_id=database.id, name="Result", type="text", position=1.0,
        )
        db.commit()
        e_napoleon  = _make_entry(db, database, "Napoleon",   position=1.0)
        e_wellington = _make_entry(db, database, "Wellington", position=2.0)
        db.commit()

        spec = {
            "mode": "where",
            "groups": [{
                "conjunction": "and",
                "filters": [{"schemaId": "__name__", "operator": "contains", "value": "leon"}],
            }],
        }
        _run_bulk(db, str(database.id), str(target.id), spec, _DONE)

        assert _value_text(db, e_napoleon.id,   target.id) == {"text": "DONE"}
        assert _value_text(db, e_wellington.id, target.id) is None


# ─── _handle_bulk_upsert_value: 'where' relation filter (issue #6) ─────────────


def test_bulk_where_relation_contains_matches_by_entry_uuid():
    """
    A relation 'contains' filter matches on the related entry's UUID, not on a
    name string searched inside the UUID pool (the bug reported in issue #6).
    """
    import app.database.database as db_module
    from app.blocks import repository as repo

    with db_module.SessionLocal() as db:
        database = _seed_database(db)
        other_db = repo.create_block(
            db, type="database", position=2.0, parent_id=database.parent_id,
        )
        db.commit()
        linked_target = _make_entry(db, other_db, "Linked Target", position=1.0)

        relation = repo.create_schema(
            db, database_id=database.id, name="Links", type="relation",
            position=1.0, config={"target_database_id": str(other_db.id)},
        )
        target = repo.create_schema(
            db, database_id=database.id, name="Result", type="text", position=2.0,
        )
        db.commit()
        e_linked   = _make_entry(db, database, "Linked",   position=1.0)
        e_unlinked = _make_entry(db, database, "Unlinked", position=2.0)
        repo.upsert_value(
            db, page_id=e_linked.id, schema_id=relation.id,
            value={"related_ids": [str(linked_target.id)]},
        )
        repo.upsert_value(
            db, page_id=e_unlinked.id, schema_id=relation.id,
            value={"related_ids": []},
        )
        db.commit()

        spec = {
            "mode": "where",
            "groups": [{
                "conjunction": "and",
                "filters": [{
                    "schemaId": str(relation.id),
                    "operator": "contains",
                    "value":    str(linked_target.id),
                }],
            }],
        }
        _run_bulk(db, str(database.id), str(target.id), spec, _DONE)

        assert _value_text(db, e_linked.id,   target.id) == {"text": "DONE"}
        assert _value_text(db, e_unlinked.id, target.id) is None


# ─── _handle_bulk_upsert_value: 'all' mode ────────────────────────────────────


def test_bulk_all_updates_every_entry_excluding_templates():
    """mode == 'all' updates all active entries but never entry_template blocks."""
    import app.database.database as db_module
    from app.blocks import repository as repo

    with db_module.SessionLocal() as db:
        database = _seed_database(db)
        target = repo.create_schema(
            db, database_id=database.id, name="Result", type="text", position=1.0,
        )
        db.commit()
        e1 = _make_entry(db, database, "One", position=1.0)
        e2 = _make_entry(db, database, "Two", position=2.0)
        tmpl = _make_entry(db, database, "Template", position=3.0, type="entry_template")
        db.commit()

        _run_bulk(db, str(database.id), str(target.id), {"mode": "all", "groups": []}, _DONE)

        assert _value_text(db, e1.id, target.id) == {"text": "DONE"}
        assert _value_text(db, e2.id, target.id) == {"text": "DONE"}
        assert _value_text(db, tmpl.id, target.id) is None


# ─── _handle_bulk_upsert_value: stale-property safety guard ───────────────────


def test_bulk_where_stale_property_aborts_without_updating():
    """
    A 'where' filter that references a property which no longer exists must abort
    the whole action rather than silently updating every entry.
    """
    import app.database.database as db_module
    from app.blocks import repository as repo

    with db_module.SessionLocal() as db:
        database = _seed_database(db)
        target = repo.create_schema(
            db, database_id=database.id, name="Result", type="text", position=1.0,
        )
        db.commit()
        e1 = _make_entry(db, database, "One", position=1.0)
        e2 = _make_entry(db, database, "Two", position=2.0)
        db.commit()

        spec = {
            "mode": "where",
            "groups": [{
                "conjunction": "and",
                "filters": [{
                    "schemaId": str(uuid.uuid4()),  # never created → stale
                    "operator": "contains",
                    "value":    "anything",
                }],
            }],
        }
        _run_bulk(db, str(database.id), str(target.id), spec, _DONE)

        assert _value_text(db, e1.id, target.id) is None
        assert _value_text(db, e2.id, target.id) is None


# ─── _handle_bulk_upsert_value: delegation contract ───────────────────────────


def test_bulk_where_relation_delegates_resolved_descriptor_to_query_entries():
    """
    The handler must resolve a relation condition into a FilterDescriptor with
    schema_type 'relation' and the entry UUID as value, then hand it to
    repository.query_entries with the server-side row cap.
    """
    import app.database.database as db_module
    from app.blocks import repository as repo

    with db_module.SessionLocal() as db:
        database = _seed_database(db)
        relation = repo.create_schema(
            db, database_id=database.id, name="Links", type="relation",
            position=1.0, config={"target_database_id": str(uuid.uuid4())},
        )
        target = repo.create_schema(
            db, database_id=database.id, name="Result", type="text", position=2.0,
        )
        db.commit()
        rel_uuid = str(uuid.uuid4())

        spec = {
            "mode": "where",
            "groups": [{
                "conjunction": "and",
                "filters": [{
                    "schemaId": str(relation.id),
                    "operator": "contains",
                    "value":    rel_uuid,
                }],
            }],
        }

        with patch(
            "app.blocks.repository.query_entries", return_value=([], 0),
        ) as mock_q, patch(
            "app.ws.broadcaster.broadcast_block_event", new_callable=AsyncMock,
        ):
            asyncio.run(_handle_bulk_upsert_value(
                db=db, db_uuid=str(database.id), schema_id=str(target.id),
                filter_spec=spec, body=_DONE,
            ))

        assert mock_q.call_count == 1
        _, called_db_id, called_groups, called_sorts = mock_q.call_args[0]
        assert str(called_db_id) == str(database.id)
        assert called_sorts == []
        assert mock_q.call_args.kwargs.get("limit") == 10_000
        assert len(called_groups) == 1
        descriptors = called_groups[0].filters
        assert len(descriptors) == 1
        assert descriptors[0].schema_type == "relation"
        assert descriptors[0].operator == "contains"
        assert descriptors[0].value == rel_uuid
