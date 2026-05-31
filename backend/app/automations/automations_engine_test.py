"""
Tests for the automation engine.

Pure unit tests cover the stateless helper functions (_matches, _render,
_render_body, _matches_actor_filter, _matches_trigger, _extract_cell_string,
_compare, _group_matches).  Integration tests use the in-memory SQLite database
provided by the autouse ``isolated_db`` fixture in conftest.py.

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
    _compare,
    _extract_cell_string,
    _group_matches,
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


# ─── _extract_cell_string ─────────────────────────────────────────────────────


def test_extract_cell_string_none():
    assert _extract_cell_string(None) == ""


def test_extract_cell_string_text():
    assert _extract_cell_string({"text": "hello"}) == "hello"


def test_extract_cell_string_option():
    assert _extract_cell_string({"option": "Done"}) == "Done"


def test_extract_cell_string_number_int():
    assert _extract_cell_string({"number": 42}) == "42"


def test_extract_cell_string_number_whole_float():
    """Whole floats are normalised to integer strings (2.0 → '2')."""
    assert _extract_cell_string({"number": 2.0}) == "2"


def test_extract_cell_string_number_decimal_float():
    assert _extract_cell_string({"number": 3.14}) == "3.14"


def test_extract_cell_string_checked_true():
    """Checkbox values are lowercased to match frontend option values."""
    assert _extract_cell_string({"checked": True}) == "true"


def test_extract_cell_string_checked_false():
    assert _extract_cell_string({"checked": False}) == "false"


def test_extract_cell_string_null_value():
    assert _extract_cell_string({"text": None}) == ""


def test_extract_cell_string_unknown_shape():
    assert _extract_cell_string({"foo": "bar"}) == "{'foo': 'bar'}"


def test_extract_cell_string_raw_string():
    assert _extract_cell_string("plain") == "plain"


# ─── _compare ────────────────────────────────────────────────────────────────


def test_compare_eq_hit():
    assert _compare("Done", "eq", "Done") is True


def test_compare_eq_numeric_int_vs_float():
    """Filter value "2" should match stored "2.0" via numeric comparison."""
    assert _compare("2.0", "eq", "2") is True
    assert _compare("2", "eq", "2.0") is True


def test_compare_eq_numeric_mismatch():
    assert _compare("2.5", "eq", "2") is False


def test_compare_neq_numeric():
    assert _compare("2.0", "neq", "3") is True
    assert _compare("2.0", "neq", "2") is False


def test_compare_eq_miss():
    assert _compare("Done", "eq", "In Progress") is False


def test_compare_neq():
    assert _compare("Done", "neq", "In Progress") is True
    assert _compare("Done", "neq", "Done") is False


def test_compare_contains():
    assert _compare("Hello World", "contains", "world") is True
    assert _compare("Hello World", "contains", "xyz") is False


def test_compare_not_contains():
    assert _compare("Hello World", "not_contains", "xyz") is True
    assert _compare("Hello World", "not_contains", "world") is False


def test_compare_starts_with():
    assert _compare("Hello World", "starts_with", "hello") is True
    assert _compare("Hello World", "starts_with", "world") is False


def test_compare_ends_with():
    assert _compare("Hello World", "ends_with", "World") is True
    assert _compare("Hello World", "ends_with", "Hello") is False


def test_compare_is_empty():
    assert _compare("", "is_empty", "") is True
    assert _compare("x", "is_empty", "") is False


def test_compare_is_not_empty():
    assert _compare("x", "is_not_empty", "") is True
    assert _compare("", "is_not_empty", "") is False


def test_compare_gt():
    assert _compare("10", "gt", "5") is True
    assert _compare("5", "gt", "10") is False
    assert _compare("abc", "gt", "1") is False  # non-numeric fails gracefully


def test_compare_gte():
    assert _compare("5", "gte", "5") is True
    assert _compare("4", "gte", "5") is False


def test_compare_lt():
    assert _compare("3", "lt", "5") is True
    assert _compare("5", "lt", "3") is False


def test_compare_lte():
    assert _compare("5", "lte", "5") is True
    assert _compare("6", "lte", "5") is False


def test_compare_unknown_operator_passes():
    """Unknown operators pass through so future operators don't block updates."""
    assert _compare("anything", "future_op", "value") is True


# ─── _group_matches ──────────────────────────────────────────────────────────


def test_group_matches_empty_filters_passes():
    assert _group_matches({}, {"conjunction": "and", "filters": []}) is True


def test_group_matches_and_all_pass():
    group = {
        "conjunction": "and",
        "filters": [
            {"schemaId": "s1", "operator": "eq",  "value": "Done"},
            {"schemaId": "s2", "operator": "neq", "value": "Low"},
        ],
    }
    ev = {"s1": {"text": "Done"}, "s2": {"text": "High"}}
    assert _group_matches(ev, group) is True


def test_group_matches_and_one_fails():
    group = {
        "conjunction": "and",
        "filters": [
            {"schemaId": "s1", "operator": "eq",  "value": "Done"},
            {"schemaId": "s2", "operator": "eq",  "value": "Low"},
        ],
    }
    ev = {"s1": {"text": "Done"}, "s2": {"text": "High"}}
    assert _group_matches(ev, group) is False


def test_group_matches_or_one_passes():
    group = {
        "conjunction": "or",
        "filters": [
            {"schemaId": "s1", "operator": "eq", "value": "Done"},
            {"schemaId": "s1", "operator": "eq", "value": "Archived"},
        ],
    }
    ev = {"s1": {"text": "Done"}}
    assert _group_matches(ev, group) is True


def test_group_matches_or_none_pass():
    group = {
        "conjunction": "or",
        "filters": [
            {"schemaId": "s1", "operator": "eq", "value": "Done"},
            {"schemaId": "s1", "operator": "eq", "value": "Archived"},
        ],
    }
    ev = {"s1": {"text": "In Progress"}}
    assert _group_matches(ev, group) is False


def test_group_matches_missing_schema_treats_as_empty():
    """A schema not present in entry_values yields an empty string -> is_empty passes."""
    group = {
        "conjunction": "and",
        "filters": [{"schemaId": "missing", "operator": "is_empty", "value": ""}],
    }
    assert _group_matches({}, group) is True


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
