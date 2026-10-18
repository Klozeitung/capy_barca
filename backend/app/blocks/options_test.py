"""
Tests for option identity: config normalisation, value cleanup after a
delete, usage counting and automation cleanup.
"""
import uuid
from datetime import datetime, timezone

import pytest

import app.database.database as db_module
from app.automations.automations_models import Automation
from app.blocks import options as opts
from app.blocks import repository as repo
from app.blocks.models import WORKSPACE_ROOT_ID, Block


# ─── Config ──────────────────────────────────────────────────────────────────


def test_plain_strings_become_options_with_ids():
    result = opts.normalize_options(["Value A", "Value B"])
    assert [o["label"] for o in result] == ["Value A", "Value B"]
    assert all(uuid.UUID(o["id"]) for o in result)


def test_given_ids_and_colours_are_kept():
    raw = [{"id": "o1", "label": "Value A", "color": "red"}]
    assert opts.normalize_options(raw) == raw


def test_empty_and_repeated_labels_are_dropped_and_labels_trimmed():
    result = opts.normalize_options([" Value A ", "", {"label": "Value A"}, {"label": None}, 3])
    assert [o["label"] for o in result] == ["Value A"]


def test_repeated_ids_are_replaced():
    result = opts.normalize_options([{"id": "o1", "label": "Value A"}, {"id": "o1", "label": "Value B"}])
    assert result[0]["id"] == "o1"
    assert result[1]["id"] != "o1"


def test_an_option_sent_without_id_keeps_the_id_of_its_label():
    """A client that does not know ids must not orphan the values."""
    previous = [{"id": "o1", "label": "Value A"}]
    result = opts.normalize_options(["Value A", "Value B"], previous)
    assert result[0]["id"] == "o1"
    assert result[1]["id"] != "o1"


def test_normalize_config_handles_select_and_nuance_and_leaves_others():
    select = opts.normalize_config("select", {"mode": "multiple", "options": ["Value A"]})
    assert select["mode"] == "multiple"
    assert select["options"][0]["label"] == "Value A"

    relation = opts.normalize_config(
        "relation", {"nuance": {"enabled": True, "options": ["Value A"], "affix1": "x"}},
    )
    assert relation["nuance"]["affix1"] == "x"
    assert relation["nuance"]["options"][0]["label"] == "Value A"

    assert opts.normalize_config("text", {"options": ["x"]}) == {"options": ["x"]}
    assert opts.normalize_config("select", None) is None


def test_removed_ids_and_relabelling_are_detected():
    old = {"options": [{"id": "o1", "label": "Value A"}, {"id": "o2", "label": "Value B"}]}
    renamed = {"options": [{"id": "o1", "label": "Value C"}, {"id": "o2", "label": "Value B"}]}
    shrunk = {"options": [{"id": "o1", "label": "Value A"}]}
    reordered = {"options": list(reversed(old["options"]))}

    assert opts.removed_ids("select", old, shrunk) == {"o2"}
    assert opts.removed_ids("select", old, renamed) == set()
    assert opts.labels_changed("select", old, renamed)
    assert opts.labels_changed("select", old, shrunk)
    assert not opts.labels_changed("select", old, reordered)


# ─── Values ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("before, after", [
    ({"option": "o1"}, None),
    ({"option": "o2"}, {"option": "o2"}),
    ({"options": ["o1", "o2"]}, {"options": ["o2"]}),
    ({"options": ["o1"]}, None),
    ({"_timeline": {"": {"option": "o1"}, "x": {"option": "o2"}}}, {"_timeline": {"x": {"option": "o2"}}}),
    ({"_timeline": {"": {"option": "o1"}}}, None),
    (None, None),
])
def test_strip_select_value(before, after):
    assert opts.strip_select_value(before, {"o1"}) == after


def test_strip_nuance_value_keeps_links_and_drops_the_nuance():
    value = {
        "related_ids": ["u1", "u2"],
        "nuances": {"u1": "o1", "u2": "o2"},
        "nuancePool": {"u1": {"": "o1"}},
        "_timeline": {"": {"related_ids": ["u1"], "nuances": {"u1": "o1"}}},
    }
    assert opts.strip_nuance_value(value, {"o1"}) == {
        "related_ids": ["u1", "u2"],
        "nuances": {"u2": "o2"},
        "_timeline": {"": {"related_ids": ["u1"]}},
    }


def test_strip_filter_groups_removes_conditions_and_empty_groups():
    groups = [
        {"conjunction": "or", "filters": [
            {"schemaId": "s1", "operator": "eq", "value": "o1"},
            {"schemaId": "s1", "operator": "eq", "value": "o2"},
        ]},
        {"conjunction": "and", "filters": [{"schemaId": "s1", "operator": "eq", "value": "o1"}]},
        {"conjunction": "and", "filters": [{"schemaId": "s2", "operator": "eq", "value": "o1"}]},
    ]
    result, changed = opts.strip_filter_groups(groups, "s1", {"o1"})
    assert changed
    assert result == [
        {"conjunction": "or", "filters": [{"schemaId": "s1", "operator": "eq", "value": "o2"}]},
        {"conjunction": "and", "filters": [{"schemaId": "s2", "operator": "eq", "value": "o1"}]},
    ]


# ─── Against the database ────────────────────────────────────────────────────


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture
def db():
    with db_module.SessionLocal() as session:
        session.add(Block(
            id=WORKSPACE_ROOT_ID, type="workspace", position=0.0, state="active",
            created_at=_now(), updated_at=_now(),
        ))
        session.commit()
        yield session


@pytest.fixture
def select_schema(db):
    database = repo.create_block(db, type="database", position=1.0, parent_id=WORKSPACE_ROOT_ID)
    schema = repo.create_schema(
        db, database_id=database.id, name="Status", type="select", position=1.0,
        config={"mode": "multiple", "options": [
            {"id": "o1", "label": "Value A"}, {"id": "o2", "label": "Value B"},
        ]},
    )
    db.commit()
    return schema


def _entry(db, schema, value, block_type="page", state="active"):
    block = repo.create_block(db, type=block_type, position=1.0, parent_id=schema.database_id)
    block.state = state
    repo.upsert_value(db, page_id=block.id, schema_id=schema.id, value=value)
    db.commit()
    return block


def test_purge_values_cleans_entries_and_templates(db, select_schema):
    entry = _entry(db, select_schema, {"options": ["o1", "o2"]})
    template = _entry(db, select_schema, {"options": ["o1"]}, block_type="entry_template")
    untouched = _entry(db, select_schema, {"options": ["o2"]})

    changed = opts.purge_values(db, select_schema, {"o1"})
    db.commit()

    assert changed == 2
    assert repo.get_value(db, entry.id, select_schema.id).value == {"options": ["o2"]}
    assert repo.get_value(db, template.id, select_schema.id).value is None
    assert repo.get_value(db, untouched.id, select_schema.id).value == {"options": ["o2"]}


def test_count_usage_counts_active_entries_only(db, select_schema):
    _entry(db, select_schema, {"options": ["o1"]})
    _entry(db, select_schema, {"options": ["o1", "o2"]})
    _entry(db, select_schema, {"options": ["o1"]}, state="trash")
    _entry(db, select_schema, {"options": ["o1"]}, block_type="entry_template")
    _entry(db, select_schema, {"options": ["o2"]})

    assert opts.count_usage(db, select_schema, "o1") == 2
    assert opts.count_usage(db, select_schema, "o3") == 0


def test_purge_automations_drops_conditions_and_written_values(db, select_schema):
    key = str(select_schema.id)
    automation = Automation(
        database_id=select_schema.database_id, name="Rule", trigger={},
        actions=[
            {"endpoint": f"PUT /api/databases/x/entries/y/values/{key}",
             "body": {"value": {"option": "o1"}}},
            {"endpoint": f"PUT /api/databases/x/bulk-values/{uuid.uuid4()}",
             "body": {"value": {"text": "x"}},
             "filter": {"mode": "where", "groups": [
                 {"conjunction": "and", "filters": [
                     {"schemaId": key, "operator": "eq", "value": "o1"},
                 ]},
             ]}},
        ],
    )
    db.add(automation)
    db.commit()

    assert opts.purge_automations(db, select_schema.id, {"o1"}) == 1
    db.commit()
    db.refresh(automation)

    assert automation.actions[0]["body"] == {"value": None}
    assert automation.actions[1]["filter"]["groups"] == []
