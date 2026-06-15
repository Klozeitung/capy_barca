"""
Tests for the computed property engine.

Uses the autouse ``isolated_db`` fixture from conftest.py for tests that touch
the database, and plain Python unit tests for the graph / cycle algorithms.
"""

import uuid

import pytest

import app.session.session as s
from app.blocks import repository as repo
from app.blocks.computed import (
    CycleError,
    SchemaLike,
    _aggregate,
    _extract_scalar,
    _infer_result_type,
    _resolve_relation_entries,
    _serialise_formula_result,
    build_dependency_graph,
    compute_all_for_entry,
    has_any_cycle,
    topological_order,
)
from app.blocks.models import WORKSPACE_ROOT_ID, Block


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    with s.SessionLocal() as session:
        yield session


@pytest.fixture
def workspace(db):
    block = Block(id=WORKSPACE_ROOT_ID, type="workspace", position=0.0)
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


@pytest.fixture
def database_block(db, workspace):
    block = repo.create_block(db, type="database", position=1.0, parent_id=workspace.id)
    db.commit()
    return block


@pytest.fixture
def entry(db, database_block):
    block = repo.create_block(db, type="page", position=1.0, parent_id=database_block.id)
    db.commit()
    return block


# ─── _infer_result_type ──────────────────────────────────────────────────────


def test_infer_result_type_datetime():
    import datetime
    dt = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
    assert _infer_result_type(dt) == "date"


def test_infer_result_type_bool_true():
    assert _infer_result_type(True) == "boolean"


def test_infer_result_type_bool_false():
    assert _infer_result_type(False) == "boolean"


def test_infer_result_type_int():
    assert _infer_result_type(42) == "number"


def test_infer_result_type_float():
    assert _infer_result_type(3.14) == "number"


def test_infer_result_type_string():
    assert _infer_result_type("hello") == "text"


def test_infer_result_type_none():
    assert _infer_result_type(None) == "text"


# ─── _serialise_formula_result ───────────────────────────────────────────────


def test_serialise_datetime_to_iso():
    import datetime
    dt = datetime.datetime(2024, 3, 15, 14, 30, 0, tzinfo=datetime.timezone.utc)
    result = _serialise_formula_result(dt)
    assert isinstance(result, str)
    assert result.startswith("2024-03-15T14:30:00")


def test_serialise_naive_datetime_to_iso():
    import datetime
    dt = datetime.datetime(2024, 6, 1, 0, 0, 0)
    result = _serialise_formula_result(dt)
    assert isinstance(result, str)
    assert "2024-06-01" in result


def test_serialise_int_passthrough():
    assert _serialise_formula_result(42) == 42


def test_serialise_float_passthrough():
    assert _serialise_formula_result(3.14) == pytest.approx(3.14)


def test_serialise_str_passthrough():
    assert _serialise_formula_result("hello") == "hello"


def test_serialise_bool_passthrough():
    assert _serialise_formula_result(True) is True


def test_serialise_none_passthrough():
    assert _serialise_formula_result(None) is None


# ─── _extract_scalar ─────────────────────────────────────────────────────────


def test_extract_scalar_text():
    assert _extract_scalar("text", {"text": "hello"}) == "hello"


def test_extract_scalar_number():
    assert _extract_scalar("number", {"number": 42}) == 42


def test_extract_scalar_checkbox():
    assert _extract_scalar("checkbox", {"checked": True}) is True


def test_extract_scalar_select_string():
    assert _extract_scalar("select", {"selected": "A"}) == "A"


def test_extract_scalar_select_list():
    assert _extract_scalar("select", {"selected": ["B", "C"]}) == "B"


def test_extract_scalar_select_option_key():
    # Current frontend format: SelectCell stores {"option": "label"}
    assert _extract_scalar("select", {"option": "A · Einkommen Block 1"}) == "A · Einkommen Block 1"


def test_extract_scalar_select_option_key_empty_string():
    # Empty option (no selection) must return "" not fall through to "selected"
    assert _extract_scalar("select", {"option": ""}) == ""


def test_extract_scalar_select_option_key_takes_priority():
    # If both keys are present, "option" wins
    assert _extract_scalar("select", {"option": "A", "selected": "B"}) == "A"


def test_extract_scalar_date():
    result = _extract_scalar("date", {"start": "2025-01-01", "end": "2025-01-31"})
    assert result == {"start": "2025-01-01", "end": "2025-01-31"}


def test_extract_scalar_date_no_end():
    result = _extract_scalar("date", {"start": "2025-01-01"})
    assert result == {"start": "2025-01-01", "end": None}


def test_extract_scalar_formula_result():
    assert _extract_scalar("formula", {"result": 99}) == 99


def test_extract_scalar_none_value():
    assert _extract_scalar("text", None) is None


def test_extract_scalar_missing_key():
    assert _extract_scalar("number", {}) is None


# ─── _aggregate ──────────────────────────────────────────────────────────────


def test_aggregate_count():
    assert _aggregate([1, 2, None], "count") == 3


def test_aggregate_count_unique():
    assert _aggregate([1, 2, 2, 3], "count_unique") == 3


def test_aggregate_sum():
    assert _aggregate([1.0, 2.0, 3.0], "sum") == 6.0


def test_aggregate_avg():
    assert _aggregate([10, 20, 30], "avg") == 20.0


def test_aggregate_min():
    assert _aggregate([5, 2, 8], "min") == 2


def test_aggregate_max():
    assert _aggregate([5, 2, 8], "max") == 8


def test_aggregate_sum_ignores_none():
    assert _aggregate([1, None, 3], "sum") == 4.0


def test_aggregate_empty_non_count_returns_none():
    assert _aggregate([], "sum") is None


def test_aggregate_all_none_non_count_returns_none():
    assert _aggregate([None, None], "sum") is None


# ── count_values / count_empty / count_not_empty ──────────────────────────────

def test_aggregate_count_values():
    assert _aggregate([1, None, 3], "count_values") == 2


def test_aggregate_count_empty():
    assert _aggregate([1, None, 3], "count_empty") == 1


def test_aggregate_count_not_empty():
    assert _aggregate([1, None, 3], "count_not_empty") == 2


def test_aggregate_count_values_all_null():
    assert _aggregate([None, None], "count_values") == 0


# ── percent family ────────────────────────────────────────────────────────────

def test_aggregate_percent_empty():
    assert _aggregate([1, None, None, 4], "percent_empty") == pytest.approx(50.0)


def test_aggregate_percent_not_empty():
    assert _aggregate([1, None, None, 4], "percent_not_empty") == pytest.approx(50.0)


def test_aggregate_percent_empty_empty_list():
    assert _aggregate([], "percent_empty") is None


def test_aggregate_percent_checked():
    assert _aggregate([True, False, True, None], "percent_checked") == pytest.approx(50.0)


def test_aggregate_percent_unchecked():
    assert _aggregate([True, False, True, None], "percent_unchecked") == pytest.approx(25.0)


def test_aggregate_percent_checked_empty_list():
    assert _aggregate([], "percent_checked") is None


def test_aggregate_percent_per_option_basic():
    result = _aggregate(["A", "B", "A", None], "percent_per_option")
    assert isinstance(result, dict)
    assert result["A"] == pytest.approx(50.0)
    assert result["B"] == pytest.approx(25.0)
    assert "None" not in result


def test_aggregate_percent_per_option_empty_list():
    assert _aggregate([], "percent_per_option") == {}


def test_aggregate_percent_per_option_all_null():
    assert _aggregate([None, None], "percent_per_option") == {}


# ── median / range ────────────────────────────────────────────────────────────

def test_aggregate_median_odd():
    assert _aggregate([1, 3, 2], "median") == pytest.approx(2.0)


def test_aggregate_median_even():
    assert _aggregate([1, 2, 3, 4], "median") == pytest.approx(2.5)


def test_aggregate_median_single():
    assert _aggregate([7], "median") == pytest.approx(7.0)


def test_aggregate_median_with_none():
    assert _aggregate([None, 1, 3, 5], "median") == pytest.approx(3.0)


def test_aggregate_median_all_none():
    assert _aggregate([None], "median") is None


def test_aggregate_range():
    assert _aggregate([10, 3, 7], "range") == pytest.approx(7.0)


def test_aggregate_range_with_none():
    assert _aggregate([None, 1, 9], "range") == pytest.approx(8.0)


def test_aggregate_range_all_none():
    assert _aggregate([None], "range") is None


# ── show_original / first_value / last_value ──────────────────────────────────

def test_aggregate_show_original():
    assert _aggregate([1, None, 3], "show_original") == [1, None, 3]


def test_aggregate_show_original_empty():
    assert _aggregate([], "show_original") == []


def test_aggregate_first_value():
    assert _aggregate([None, 2, 3], "first_value") == 2


def test_aggregate_first_value_all_none():
    assert _aggregate([None], "first_value") is None


def test_aggregate_last_value():
    assert _aggregate([1, 2, None], "last_value") == 2


def test_aggregate_last_value_all_none():
    assert _aggregate([None], "last_value") is None


# ── empty-list behaviour consistent across all function families ──────────────

def test_aggregate_count_empty_list():
    assert _aggregate([], "count") == 0


def test_aggregate_count_values_empty_list():
    assert _aggregate([], "count_values") == 0


def test_aggregate_show_original_preserves_order():
    assert _aggregate(["c", "a", "b"], "show_original") == ["c", "a", "b"]


# ── _resolve_relation_entries (relation rollup → clickable chips, #11) ────────

def _fake_entry_resolver(mapping: dict[str, dict | None]):
    """Return a resolver mapping known IDs to descriptors, unknown IDs to None."""
    return lambda rid: mapping.get(rid, None)


def _desc(id_: str, title: str, db_: str = "db1") -> dict:
    return {"id": id_, "title": title, "database_id": db_}


def test_resolve_relation_entries_flattens_and_maps():
    scalars = [["a", "b"], ["c"]]
    resolver = _fake_entry_resolver({
        "a": _desc("a", "Alice"), "b": _desc("b", "Bob"), "c": _desc("c", "Carol"),
    })
    assert _resolve_relation_entries(scalars, resolver) == [
        _desc("a", "Alice"), _desc("b", "Bob"), _desc("c", "Carol"),
    ]


def test_resolve_relation_entries_skips_missing():
    scalars = [["a", "b"]]
    resolver = _fake_entry_resolver({"a": _desc("a", "Alice"), "b": None})
    assert _resolve_relation_entries(scalars, resolver) == [_desc("a", "Alice")]


def test_resolve_relation_entries_skips_empty_scalars():
    scalars = [None, [], ["a"]]
    resolver = _fake_entry_resolver({"a": _desc("a", "Alice")})
    assert _resolve_relation_entries(scalars, resolver) == [_desc("a", "Alice")]


def test_resolve_relation_entries_empty_input():
    assert _resolve_relation_entries([], _fake_entry_resolver({})) == []


# ─── build_dependency_graph ───────────────────────────────────────────────────


def _schema(name: str, type_: str, config: dict | None = None) -> SchemaLike:
    return SchemaLike(id=uuid.uuid4(), name=name, type=type_, config=config)


def test_graph_empty_for_no_computed():
    schemas = [_schema("Name", "text"), _schema("Count", "number")]
    assert build_dependency_graph(schemas) == {}


def test_graph_formula_no_expression():
    s = _schema("Total", "formula", {"expression": ""})
    graph = build_dependency_graph([s])
    assert graph[s.id] == set()


def test_graph_formula_resolves_deps():
    price = _schema("Price", "number")
    qty = _schema("Qty", "number")
    total = _schema("Total", "formula", {"expression": "prop('Price') * prop('Qty')"})
    graph = build_dependency_graph([price, qty, total])
    assert graph[total.id] == {price.id, qty.id}


def test_graph_formula_ignores_unknown_props():
    total = _schema("Total", "formula", {"expression": "prop('NonExistent') + 1"})
    graph = build_dependency_graph([total])
    assert graph[total.id] == set()


def test_graph_formula_no_self_reference_in_deps():
    s = _schema("X", "formula", {"expression": "prop('X') + 1"})
    graph = build_dependency_graph([s])
    # Self-references are excluded from the dep set
    assert s.id not in graph[s.id]


def test_graph_rollup_has_relation_dep():
    rel = _schema("Projects", "relation")
    rollup = _schema("Count", "rollup", {
        "relation_schema_id": str(rel.id),
        "rollup_schema_id": str(uuid.uuid4()),
        "function": "count",
    })
    graph = build_dependency_graph([rel, rollup])
    assert rel.id in graph[rollup.id]


# ─── has_any_cycle ────────────────────────────────────────────────────────────


def test_no_cycle_linear():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    graph = {b: {a}, c: {b}}  # a → b → c (no cycle)
    assert not has_any_cycle(graph)


def test_no_cycle_diamond():
    a, b, c, d = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    graph = {b: {a}, c: {a}, d: {b, c}}
    assert not has_any_cycle(graph)


def test_cycle_simple():
    a, b = uuid.uuid4(), uuid.uuid4()
    graph = {a: {b}, b: {a}}
    assert has_any_cycle(graph)


def test_cycle_self_reference():
    a = uuid.uuid4()
    graph = {a: {a}}
    assert has_any_cycle(graph)


def test_cycle_three_node():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    graph = {a: {c}, b: {a}, c: {b}}
    assert has_any_cycle(graph)


def test_no_cycle_empty_graph():
    assert not has_any_cycle({})


# ─── topological_order ────────────────────────────────────────────────────────


def test_topological_order_simple():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    graph = {b: {a}, c: {b}}
    order = topological_order(graph)
    assert order.index(b) < order.index(c)


def test_topological_order_includes_only_keys():
    a, b = uuid.uuid4(), uuid.uuid4()
    graph = {b: {a}}  # a is a dep but not a computed schema
    order = topological_order(graph)
    assert b in order
    assert a not in order


def test_topological_order_cycle_raises():
    a, b = uuid.uuid4(), uuid.uuid4()
    graph = {a: {b}, b: {a}}
    with pytest.raises(CycleError):
        topological_order(graph)


def test_topological_order_empty():
    assert topological_order({}) == []


# ─── compute_all_for_entry (integration) ─────────────────────────────────────


def test_formula_simple_multiplication(db, database_block, entry):
    price = repo.create_schema(
        db, database_id=database_block.id, name="Price", type="number",
        position=1.0, config={"format": "plain"},
    )
    qty = repo.create_schema(
        db, database_id=database_block.id, name="Qty", type="number",
        position=2.0, config={"format": "plain"},
    )
    total = repo.create_schema(
        db, database_id=database_block.id, name="Total", type="formula",
        position=3.0, config={"expression": "prop('Price') * prop('Qty')"},
    )
    db.commit()

    repo.upsert_value(db, page_id=entry.id, schema_id=price.id, value={"number": 9.99})
    repo.upsert_value(db, page_id=entry.id, schema_id=qty.id, value={"number": 3})
    db.commit()

    compute_all_for_entry(db, database_block.id, entry.id)
    db.commit()

    pv = repo.get_value(db, entry.id, total.id)
    assert pv is not None
    assert pv.value["result"] == pytest.approx(29.97)
    assert pv.value["result_type"] == "number"
    assert "error" not in pv.value


def test_formula_missing_prop_treated_as_zero(db, database_block, entry):
    schema = repo.create_schema(
        db, database_id=database_block.id, name="Ref", type="formula",
        position=1.0, config={"expression": "prop('Missing') + 1"},
    )
    db.commit()

    compute_all_for_entry(db, database_block.id, entry.id)
    db.commit()

    pv = repo.get_value(db, entry.id, schema.id)
    assert pv is not None
    # An empty (missing) source property counts as 0 in a numeric context,
    # so the formula evaluates to a value instead of surfacing a type error.
    assert pv.value["result"] == 1
    assert pv.value["result_type"] == "number"
    assert "error" not in pv.value


def test_formula_chained_formulas_evaluate_in_order(db, database_block, entry):
    base = repo.create_schema(
        db, database_id=database_block.id, name="Base", type="number",
        position=1.0, config=None,
    )
    doubled = repo.create_schema(
        db, database_id=database_block.id, name="Doubled", type="formula",
        position=2.0, config={"expression": "prop('Base') * 2"},
    )
    quadrupled = repo.create_schema(
        db, database_id=database_block.id, name="Quadrupled", type="formula",
        position=3.0, config={"expression": "prop('Doubled') * 2"},
    )
    db.commit()

    repo.upsert_value(db, page_id=entry.id, schema_id=base.id, value={"number": 5})
    db.commit()

    compute_all_for_entry(db, database_block.id, entry.id)
    db.commit()

    pv_q = repo.get_value(db, entry.id, quadrupled.id)
    assert pv_q.value["result"] == pytest.approx(20.0)


def test_formula_cycle_stores_error(db, database_block, entry):
    a = repo.create_schema(
        db, database_id=database_block.id, name="A", type="formula",
        position=1.0, config={"expression": "prop('B') + 1"},
    )
    b = repo.create_schema(
        db, database_id=database_block.id, name="B", type="formula",
        position=2.0, config={"expression": "prop('A') + 1"},
    )
    db.commit()

    compute_all_for_entry(db, database_block.id, entry.id)
    db.commit()

    pv_a = repo.get_value(db, entry.id, a.id)
    pv_b = repo.get_value(db, entry.id, b.id)
    assert pv_a.value.get("error") is not None
    assert pv_b.value.get("error") is not None


def test_no_computed_schemas_is_noop(db, database_block, entry):
    repo.create_schema(
        db, database_id=database_block.id, name="Name", type="text",
        position=1.0, config=None,
    )
    db.commit()

    # Should not raise and should not create any computed values
    compute_all_for_entry(db, database_block.id, entry.id)
    db.commit()

    values = repo.list_values(db, entry.id)
    assert values == []


# ── date aggregations ─────────────────────────────────────────────────────────

def test_aggregate_earliest_date():
    dates = ["2024-03-01", "2023-12-25", None, "2024-01-15"]
    result = _aggregate(dates, "earliest_date")
    assert result is not None
    assert result.startswith("2023-12-25")


def test_aggregate_latest_date():
    dates = ["2024-03-01", "2023-12-25", None, "2024-01-15"]
    result = _aggregate(dates, "latest_date")
    assert result is not None
    assert result.startswith("2024-03-01")


def test_aggregate_date_range_different():
    dates = ["2024-01-01", "2024-12-31"]
    result = _aggregate(dates, "date_range")
    assert result == "2024-01-01 → 2024-12-31"


def test_aggregate_date_range_same():
    dates = ["2024-06-15", "2024-06-15"]
    result = _aggregate(dates, "date_range")
    assert result == "2024-06-15"


def test_aggregate_date_all_none():
    assert _aggregate([None, None], "earliest_date") is None


# ── _extract_scalar relation ──────────────────────────────────────────────────

def test_extract_scalar_relation_with_ids():
    ids = ["uuid-1", "uuid-2"]
    result = _extract_scalar("relation", {"related_ids": ids})
    assert result == ids


def test_extract_scalar_relation_empty():
    result = _extract_scalar("relation", {"related_ids": []})
    assert result == []


def test_extract_scalar_relation_missing_key():
    result = _extract_scalar("relation", {})
    assert result == []


# ── Timeline helpers ──────────────────────────────────────────────────────────

from app.blocks.computed import (
    _last_timeline_slot,
    _get_related_ids,
    _entry_id_in_relation_value,
)


class _FakeSchema:
    def __init__(self, config=None):
        self.config = config or {}


# _last_timeline_slot

def test_last_timeline_slot_empty():
    assert _last_timeline_slot({}) is None


def test_last_timeline_slot_singleton_empty_key():
    val = {"text": "hello"}
    assert _last_timeline_slot({"": val}) is val


def test_last_timeline_slot_picks_latest_start():
    a = {"text": "old"}
    b = {"text": "new"}
    timeline = {
        "2023-01-01T00:00:00→2023-12-31T23:59:59": a,
        "2024-01-01T00:00:00→": b,
    }
    assert _last_timeline_slot(timeline) is b


def test_last_timeline_slot_until_range_is_earliest():
    until = {"text": "before"}
    since = {"text": "after"}
    timeline = {
        "→2022-12-31T23:59:59": until,
        "2023-01-01T00:00:00→": since,
    }
    assert _last_timeline_slot(timeline) is since


# _get_related_ids

def test_get_related_ids_plain():
    schema = _FakeSchema({"hasTimeline": False})
    val = {"related_ids": ["a", "b"]}
    assert _get_related_ids(val, schema) == ["a", "b"]


def test_get_related_ids_none_value():
    schema = _FakeSchema()
    assert _get_related_ids(None, schema) == []


def test_get_related_ids_timeline_last_slot():
    schema = _FakeSchema({"hasTimeline": True})
    val = {
        "_timeline": {
            "2023-01-01T00:00:00→2023-12-31T23:59:59": {"related_ids": ["old"]},
            "2024-01-01T00:00:00→": {"related_ids": ["new1", "new2"]},
        }
    }
    result = _get_related_ids(val, schema)
    assert result == ["new1", "new2"]


def test_get_related_ids_timeline_empty():
    schema = _FakeSchema({"hasTimeline": True})
    val = {"_timeline": {}}
    assert _get_related_ids(val, schema) == []


# _entry_id_in_relation_value

def test_entry_id_in_plain_relation():
    val = {"related_ids": ["uuid-a", "uuid-b"]}
    assert _entry_id_in_relation_value("uuid-a", val) is True
    assert _entry_id_in_relation_value("uuid-z", val) is False


def test_entry_id_in_timeline_relation():
    val = {
        "_timeline": {
            "2023-01-01T00:00:00→": {"related_ids": ["uuid-c"]},
            "2024-01-01T00:00:00→2024-12-31T23:59:59": {"related_ids": ["uuid-a", "uuid-c"]},
        }
    }
    assert _entry_id_in_relation_value("uuid-a", val) is True
    assert _entry_id_in_relation_value("uuid-z", val) is False


def test_entry_id_in_none_value():
    assert _entry_id_in_relation_value("uuid-a", None) is False


# _extract_scalar with timeline-aware config

def test_extract_scalar_relation_timeline_last_slot():
    config = {"hasTimeline": True}
    val = {
        "_timeline": {
            "2023-01-01T00:00:00→": {"related_ids": ["uuid-x"]},
        }
    }
    result = _extract_scalar("relation", val, config)
    assert result == ["uuid-x"]


def test_extract_scalar_relation_no_timeline_config():
    config = {"hasTimeline": False}
    val = {"related_ids": ["uuid-y"]}
    result = _extract_scalar("relation", val, config)
    assert result == ["uuid-y"]


# ─── _aggregate: checked ──────────────────────────────────────────────────────


def test_aggregate_checked_counts_true():
    assert _aggregate([True, False, True, None], "checked") == 2


def test_aggregate_checked_all_false():
    assert _aggregate([False, False], "checked") == 0


def test_aggregate_checked_empty_list():
    assert _aggregate([], "checked") == 0


def test_aggregate_checked_all_none():
    assert _aggregate([None, None], "checked") == 0


def test_aggregate_checked_all_true():
    assert _aggregate([True, True, True], "checked") == 3
