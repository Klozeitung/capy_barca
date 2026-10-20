"""
Tests for the pure helpers of the schema service.

The database steps (mirror propagation, timeline migration, formula
reference rewrite) are covered end to end through the schema endpoints in
``database_router_test.py``.
"""
import pytest
from fastapi import HTTPException

from app.blocks import schema_service


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
