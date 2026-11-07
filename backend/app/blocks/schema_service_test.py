"""
Tests for the pure helpers of the schema service.

The database steps (mirror propagation, timeline migration, binding formula
references to property ids) are covered end to end through the schema
endpoints in ``database_router_test.py``.
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.blocks import kinship, schema_service


# ─── Validation ──────────────────────────────────────────────────────────────


def test_formula_with_a_syntax_error_is_refused():
    with pytest.raises(HTTPException) as exc:
        schema_service.validate_computed_config("formula", {"expression": "prop("})
    assert exc.value.status_code == 422


def test_rollup_with_an_unknown_function_is_refused():
    with pytest.raises(HTTPException) as exc:
        schema_service.validate_computed_config("rollup", {"function": "no_such_function"})
    assert exc.value.status_code == 422


def test_valid_and_empty_computed_configs_pass():
    schema_service.validate_computed_config("formula", {"expression": "1 + 1"})
    schema_service.validate_computed_config("formula", None)
    schema_service.validate_computed_config("rollup", {"function": ""})
    schema_service.validate_computed_config("text", {"expression": "prop("})


@pytest.mark.parametrize("old_type, new_type, expected", [
    ("date", "checkbox", True),
    ("text", "relation", True),
    ("date", "number", False),
    ("date", None, False),
    ("date", "date", False),
    ("checkbox", "url", False),
])
def test_retyped_out_of_keyable(old_type, new_type, expected):
    assert schema_service.retyped_out_of_keyable(old_type, new_type) is expected


# ─── Options ─────────────────────────────────────────────────────────────────


def _select(*options):
    return {"options": [{"id": option_id, "label": label} for option_id, label in options]}


def test_option_changes_reports_removed_ids_and_relabelling():
    old = _select(("id-a", "Value A"), ("id-b", "Value B"))
    removed, relabelled = schema_service.option_changes(
        "select", "select", old, _select(("id-a", "Value C")),
    )
    assert removed == {"id-b"}
    assert relabelled is True


def test_option_changes_ignores_a_retype_and_an_absent_config():
    old = _select(("id-a", "Value A"))
    assert schema_service.option_changes("select", "text", old, {}) == (set(), False)
    assert schema_service.option_changes("select", "select", old, None) == (set(), False)


# ─── Mirrored nuance ─────────────────────────────────────────────────────────


def test_mirrored_nuance_swaps_the_framing_and_shares_the_options():
    options = [{"id": "id-a", "label": "Value A"}]
    source = {
        "enabled": True,
        "options": options,
        "affix1": "as",
        "affix2": "",
        "orientation": "appended",
        "synced": {"affix1": "of", "affix2": "for", "orientation": "prepended"},
    }
    assert schema_service._mirrored_nuance(source) == {
        "enabled": True,
        "options": options,
        "affix1": "of",
        "affix2": "for",
        "orientation": "prepended",
        "synced": {"affix1": "as", "affix2": "", "orientation": "appended"},
    }


def test_mirrored_nuance_of_a_disabled_source_is_disabled():
    assert schema_service._mirrored_nuance({"enabled": False, "options": []}) == {"enabled": False}
    assert schema_service._mirrored_nuance({}) == {"enabled": False}


# ─── Formula references as the client sees them ──────────────────────────────


def _schema(name, type_="number", config=None):
    return SimpleNamespace(id=uuid.uuid4(), name=name, type=type_, config=config)


def test_client_config_shows_a_formula_with_names_and_keeps_the_stored_one():
    amount = _schema("Amount")
    stored = {"expression": f"prop('{amount.id}') + 1", "format": "plain"}
    formula = _schema("Next", "formula", stored)

    shown = schema_service.client_config(formula, [amount, formula])

    assert shown == {"expression": "prop('Amount') + 1", "format": "plain"}
    assert formula.config["expression"] == f"prop('{amount.id}') + 1"


def test_client_config_keeps_a_reference_to_no_property_as_written():
    formula = _schema("Next", "formula", {"expression": "prop('Missing') + 1"})
    assert schema_service.client_config(formula, [formula]) == {"expression": "prop('Missing') + 1"}


def test_client_config_returns_other_configs_unchanged():
    config = {"mode": "single", "options": []}
    select = _schema("Choice", "select", config)
    assert schema_service.client_config(select, [select]) is config


# ─── Alerts ──────────────────────────────────────────────────────────────────


def test_single_alerts_check_ignores_other_types(monkeypatch):
    import app.alerts.alerts_repository as alerts_repo

    monkeypatch.setattr(alerts_repo, "find_alerts_schema", lambda db, database_id: pytest.fail())
    schema_service.check_single_alerts_property(None, uuid.uuid4(), "text")
    schema_service.check_single_alerts_property(None, uuid.uuid4(), None)


def test_single_alerts_check_refuses_a_second_and_admits_the_existing_one(monkeypatch):
    import app.alerts.alerts_repository as alerts_repo

    existing = _schema("Reminders", "alerts")
    monkeypatch.setattr(alerts_repo, "find_alerts_schema", lambda db, database_id: existing)

    with pytest.raises(HTTPException) as exc:
        schema_service.check_single_alerts_property(None, uuid.uuid4(), "alerts")
    assert exc.value.status_code == 409
    schema_service.check_single_alerts_property(None, uuid.uuid4(), "alerts", existing.id)


def test_alerts_retype_without_a_type_change_touches_nothing():
    schema = _schema("Reminders", "alerts")
    assert schema_service.apply_alerts_retype(None, schema, "alerts", None) is False
    assert schema_service.apply_alerts_retype(None, schema, "alerts", "alerts") is False


# ─── Family set ──────────────────────────────────────────────────────────────


def test_keep_server_owned_config_keeps_the_family_markers():
    old = {kinship.ROLE_KEY: kinship.BIRTH}
    new = {"includeTime": True, kinship.ROLE_KEY: kinship.DEATH}
    assert schema_service.keep_server_owned_config(old, new) == {
        "includeTime": True, kinship.ROLE_KEY: kinship.BIRTH,
    }
    assert schema_service.keep_server_owned_config(None, new) == {"includeTime": True}


def test_client_config_adds_the_system_nuances_of_a_marked_relation():
    config = {"direction": "bilateral_self", kinship.ROLE_KEY: kinship.SIBLINGS}
    catalogue = kinship.catalogue(
        [{"id": "a-1", "group": kinship.GROUP_SIBLINGS, "base": "kin.sibling.step", "label": "Label A"}], "en",
    )
    shown = schema_service.client_config(_schema("Siblings", "relation", config), [], catalogue)
    assert shown["nuance"]["enabled"] is False
    assert shown["nuance"]["options"][0] == {"id": "kin.sibling.biological", "label": "biological"}
    assert shown["nuance"]["options"][-1] == {"id": "a-1", "label": "Label A"}
    assert "nuance" not in config


def test_portable_config_leaves_the_family_markers_behind():
    schema = _schema("Sex (biological)", "select", {
        "options": [], kinship.ROLE_KEY: kinship.SEX, kinship.FEMALE_OPTION_KEY: "o-1",
    })
    assert schema_service.portable_config(None, schema) == {"options": []}
