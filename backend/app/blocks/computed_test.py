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
    _resolve_formula_relation,
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


def test_extract_scalar_select_options_key_multi():
    # Multi-select frontend format: {"options": ["label", …]} — first label
    # is returned so prop('Typ') == "Geburt" works for the single-value case.
    assert _extract_scalar("select", {"options": ["Geburt"]}) == "Geburt"


def test_extract_scalar_select_options_key_multiple_values():
    assert _extract_scalar("select", {"options": ["Geburt", "Tod"]}) == "Geburt"


def test_extract_scalar_select_options_key_empty_list():
    assert _extract_scalar("select", {"options": []}) is None


def test_extract_scalar_select_options_key_after_option():
    # "option" (single) still takes priority over "options" (multi) when both
    # are present, preserving single-select semantics.
    assert _extract_scalar("select", {"option": "X", "options": ["Y"]}) == "X"


def test_extract_scalar_date():
    result = _extract_scalar("date", {"start": "2025-01-01", "end": "2025-01-31"})
    assert result == {"start": "2025-01-01", "end": "2025-01-31"}


def test_extract_scalar_date_no_end():
    result = _extract_scalar("date", {"start": "2025-01-01"})
    assert result == {"start": "2025-01-01", "end": None}


def test_extract_scalar_formula_result():
    assert _extract_scalar("formula", {"result": 99}) == 99


def test_extract_scalar_formula_relation_result_returns_ids():
    # A formula whose result is a relation-descriptor list (relation=True) is
    # extracted as a plain list of related entry IDs, so that rolling it up
    # routes through the relation chip path instead of [object Object].
    value = {
        "result": [
            {"id": "u1", "title": "Vyrenell", "database_id": "db"},
            {"id": "u2", "title": "Other", "database_id": "db"},
        ],
        "relation": True,
        "result_type": "text",
    }
    assert _extract_scalar("formula", value) == ["u1", "u2"]


def test_extract_scalar_formula_relation_empty_list():
    value = {"result": [], "relation": True, "result_type": "text"}
    assert _extract_scalar("formula", value) == []


def test_extract_scalar_formula_non_relation_string_passthrough():
    # The else-branch of a relation formula stores a plain "" — must not be
    # mistaken for a relation result.
    value = {"result": "", "result_type": "text"}
    assert _extract_scalar("formula", value) == ""


def test_extract_scalar_rollup_relation_result_returns_ids():
    value = {
        "result": [{"id": "u9", "title": "X", "database_id": "db"}],
        "relation": True,
        "function": "show_original",
    }
    assert _extract_scalar("rollup", value) == ["u9"]


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


# ─── _resolve_formula_relation ────────────────────────────────────────────────
#
# Pure unit tests using a fake resolver — no database needed.


def _fake_formula_resolver(mapping: dict[str, dict | None]):
    """Returns a resolver that maps UUID strings to descriptors or None."""
    return lambda rid: mapping.get(rid)


def _rdesc(id_: str, title: str = "", db_: str | None = "db1") -> dict:
    return {"id": id_, "title": title, "database_id": db_}


def test_resolve_formula_relation_uuid_list_resolved():
    resolver = _fake_formula_resolver({
        "00000000-0000-0000-0000-000000000001": _rdesc("00000000-0000-0000-0000-000000000001", "Alice"),
        "00000000-0000-0000-0000-000000000002": _rdesc("00000000-0000-0000-0000-000000000002", "Bob"),
    })
    result, is_rel = _resolve_formula_relation(
        ["00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"],
        resolver,
    )
    assert is_rel is True
    assert len(result) == 2
    assert result[0]["title"] == "Alice"
    assert result[1]["title"] == "Bob"


def test_resolve_formula_relation_uuid_list_skips_missing():
    resolver = _fake_formula_resolver({
        "00000000-0000-0000-0000-000000000001": _rdesc("00000000-0000-0000-0000-000000000001", "Alice"),
        "00000000-0000-0000-0000-000000000002": None,
    })
    result, is_rel = _resolve_formula_relation(
        ["00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"],
        resolver,
    )
    assert is_rel is True
    assert len(result) == 1
    assert result[0]["title"] == "Alice"


def test_resolve_formula_relation_already_resolved_list():
    descriptors = [
        _rdesc("a", "Alpha"),
        _rdesc("b", "Beta"),
    ]
    result, is_rel = _resolve_formula_relation(descriptors, lambda _: None)
    assert is_rel is True
    assert result is descriptors


def test_resolve_formula_relation_single_descriptor_wrapped():
    desc = _rdesc("a", "Alpha")
    result, is_rel = _resolve_formula_relation(desc, lambda _: None)
    assert is_rel is True
    assert result == [desc]


def test_resolve_formula_relation_plain_string_list_passthrough():
    plain = ["hello", "world"]
    result, is_rel = _resolve_formula_relation(plain, lambda _: None)
    assert is_rel is False
    assert result is plain


def test_resolve_formula_relation_empty_list_passthrough():
    result, is_rel = _resolve_formula_relation([], lambda _: None)
    assert is_rel is False
    assert result == []


def test_resolve_formula_relation_scalar_passthrough():
    result, is_rel = _resolve_formula_relation("hello", lambda _: None)
    assert is_rel is False
    assert result == "hello"


def test_resolve_formula_relation_none_passthrough():
    result, is_rel = _resolve_formula_relation(None, lambda _: None)
    assert is_rel is False
    assert result is None


def test_resolve_formula_relation_number_passthrough():
    result, is_rel = _resolve_formula_relation(42, lambda _: None)
    assert is_rel is False
    assert result == 42


def test_resolve_formula_relation_mixed_list_non_uuid_passthrough():
    # A list where one item is not a valid UUID should pass through unchanged.
    mixed = ["00000000-0000-0000-0000-000000000001", "not-a-uuid"]
    result, is_rel = _resolve_formula_relation(mixed, lambda _: None)
    assert is_rel is False
    assert result is mixed


# ─── compute_all_for_entry: formula returning relation prop (#20) ─────────────


def test_formula_relation_prop_resolves_to_chips(db, database_block, entry):
    """A formula that returns prop('Rel') yields resolved chips with relation=True."""
    # Create a second entry that will be the relation target
    target = repo.create_block(db, type="page", position=2.0, parent_id=database_block.id)
    target.content = {"title": "Target Entry"}
    db.commit()

    rel_schema = repo.create_schema(
        db, database_id=database_block.id, name="Rel", type="relation",
        position=1.0, config={"target_database_id": str(database_block.id)},
    )
    formula_schema = repo.create_schema(
        db, database_id=database_block.id, name="RelFormula", type="formula",
        position=2.0, config={"expression": "prop('Rel')"},
    )
    db.commit()

    # Link entry -> target via the relation property
    repo.upsert_value(
        db, page_id=entry.id, schema_id=rel_schema.id,
        value={"related_ids": [str(target.id)]},
    )
    db.commit()

    compute_all_for_entry(db, database_block.id, entry.id)
    db.commit()

    pv = repo.get_value(db, entry.id, formula_schema.id)
    assert pv is not None
    assert pv.value.get("relation") is True
    assert isinstance(pv.value["result"], list)
    assert len(pv.value["result"]) == 1
    assert pv.value["result"][0]["id"] == str(target.id)
    assert pv.value["result"][0]["title"] == "Target Entry"
    assert "error" not in pv.value


def test_formula_empty_relation_prop_stays_empty(db, database_block, entry):
    """A formula returning an empty relation prop should produce no relation chips."""
    rel_schema = repo.create_schema(
        db, database_id=database_block.id, name="Rel", type="relation",
        position=1.0, config={"target_database_id": str(database_block.id)},
    )
    formula_schema = repo.create_schema(
        db, database_id=database_block.id, name="RelFormula", type="formula",
        position=2.0, config={"expression": "prop('Rel')"},
    )
    db.commit()

    repo.upsert_value(
        db, page_id=entry.id, schema_id=rel_schema.id,
        value={"related_ids": []},
    )
    db.commit()

    compute_all_for_entry(db, database_block.id, entry.id)
    db.commit()

    pv = repo.get_value(db, entry.id, formula_schema.id)
    assert pv is not None
    # Empty list is not a relation result — no chips, no relation flag.
    assert pv.value.get("relation") is not True


def test_formula_multiselect_condition_returns_relation_chips(db, database_block, entry):
    """Regression: a multi-select ({"options": [...]}) condition that gates a
    relation prop must fire, so the relation target renders as chips.

    Mirrors the real-world formula:
        if(prop('Typ') == "Geburt", prop('Ort'), "")
    where 'Typ' is a multi-select storing {"options": ["Geburt"]}.
    """
    target = repo.create_block(db, type="page", position=2.0, parent_id=database_block.id)
    target.content = {"title": "Vyrenell"}
    db.commit()

    typ = repo.create_schema(
        db, database_id=database_block.id, name="Typ", type="select",
        position=1.0, config={"mode": "multiple", "options": [{"label": "Geburt"}]},
    )
    ort = repo.create_schema(
        db, database_id=database_block.id, name="Ort", type="relation",
        position=2.0, config={"target_database_id": str(database_block.id)},
    )
    formula = repo.create_schema(
        db, database_id=database_block.id, name="Formel", type="formula",
        position=3.0, config={"expression": "if(prop('Typ')==\"Geburt\",prop('Ort'),\"\")"},
    )
    db.commit()

    repo.upsert_value(db, page_id=entry.id, schema_id=typ.id, value={"options": ["Geburt"]})
    repo.upsert_value(
        db, page_id=entry.id, schema_id=ort.id,
        value={"related_ids": [str(target.id)]},
    )
    db.commit()

    compute_all_for_entry(db, database_block.id, entry.id)
    db.commit()

    pv = repo.get_value(db, entry.id, formula.id)
    assert pv is not None
    assert pv.value.get("relation") is True
    assert len(pv.value["result"]) == 1
    assert pv.value["result"][0]["title"] == "Vyrenell"


def test_formula_multiselect_condition_not_met_stays_empty(db, database_block, entry):
    """When the multi-select value does not match, the else branch ("") wins."""
    target = repo.create_block(db, type="page", position=2.0, parent_id=database_block.id)
    target.content = {"title": "Vyrenell"}
    db.commit()

    typ = repo.create_schema(
        db, database_id=database_block.id, name="Typ", type="select",
        position=1.0, config={"mode": "multiple", "options": [{"label": "Tod"}]},
    )
    ort = repo.create_schema(
        db, database_id=database_block.id, name="Ort", type="relation",
        position=2.0, config={"target_database_id": str(database_block.id)},
    )
    formula = repo.create_schema(
        db, database_id=database_block.id, name="Formel", type="formula",
        position=3.0, config={"expression": "if(prop('Typ')==\"Geburt\",prop('Ort'),\"\")"},
    )
    db.commit()

    repo.upsert_value(db, page_id=entry.id, schema_id=typ.id, value={"options": ["Tod"]})
    repo.upsert_value(
        db, page_id=entry.id, schema_id=ort.id,
        value={"related_ids": [str(target.id)]},
    )
    db.commit()

    compute_all_for_entry(db, database_block.id, entry.id)
    db.commit()

    pv = repo.get_value(db, entry.id, formula.id)
    assert pv is not None
    assert pv.value.get("relation") is not True
    assert pv.value["result"] == ""


def test_rollup_over_relation_formula_resolves_chips_and_skips_empty(db, workspace):
    """Regression: rolling up a formula column whose result is a relation
    descriptor list must render clickable chips (not [object Object]) and must
    not emit placeholder chips for entries whose formula result is empty.

    Setup mirrors the real data:
      Plot DB        : entries with an 'Ort' relation and a formula
                       'Geburtsort Formel' = prop('Ort')
      Characters DB  : 'Plot' relation -> Plot entries
                       'Geburtsort Test' rollup (show_original) over the
                       Plot formula column.
    """
    # ── Target DB that 'Ort' points at (the birthplace entries) ───────────────
    places_db = repo.create_block(db, type="database", position=1.0, parent_id=workspace.id)
    db.commit()
    vyrenell = repo.create_block(db, type="page", position=1.0, parent_id=places_db.id)
    vyrenell.content = {"title": "Vyrenell"}
    db.commit()

    # ── Plot DB: Ort relation + formula returning prop('Ort') ─────────────────
    plot_db = repo.create_block(db, type="database", position=2.0, parent_id=workspace.id)
    db.commit()
    ort = repo.create_schema(
        db, database_id=plot_db.id, name="Ort", type="relation",
        position=1.0, config={"target_database_id": str(places_db.id)},
    )
    geburtsort_formel = repo.create_schema(
        db, database_id=plot_db.id, name="Geburtsort Formel", type="formula",
        position=2.0, config={"expression": "prop('Ort')"},
    )
    db.commit()

    # Plot entry WITH a birthplace
    plot_with = repo.create_block(db, type="page", position=1.0, parent_id=plot_db.id)
    plot_with.content = {"title": "Geburt Alfred"}
    db.commit()
    repo.upsert_value(
        db, page_id=plot_with.id, schema_id=ort.id,
        value={"related_ids": [str(vyrenell.id)]},
    )
    # Plot entry WITHOUT a birthplace (empty Ort)
    plot_without = repo.create_block(db, type="page", position=2.0, parent_id=plot_db.id)
    plot_without.content = {"title": "Alfred Test"}
    db.commit()
    repo.upsert_value(
        db, page_id=plot_without.id, schema_id=ort.id, value={"related_ids": []},
    )
    db.commit()

    compute_all_for_entry(db, plot_db.id, plot_with.id)
    compute_all_for_entry(db, plot_db.id, plot_without.id)
    db.commit()

    # ── Characters DB: Plot relation + rollup (show_original) over formula ────
    chars_db = repo.create_block(db, type="database", position=3.0, parent_id=workspace.id)
    db.commit()
    plot_rel = repo.create_schema(
        db, database_id=chars_db.id, name="Plot", type="relation",
        position=1.0, config={"target_database_id": str(plot_db.id)},
    )
    geburtsort_test = repo.create_schema(
        db, database_id=chars_db.id, name="Geburtsort Test", type="rollup",
        position=2.0, config={
            "relation_schema_id": str(plot_rel.id),
            "rollup_schema_id": str(geburtsort_formel.id),
            "function": "show_original",
        },
    )
    db.commit()

    # Character linked to BOTH plot entries (one with, one without birthplace)
    prince = repo.create_block(db, type="page", position=1.0, parent_id=chars_db.id)
    prince.content = {"title": "Prince Alfred"}
    db.commit()
    repo.upsert_value(
        db, page_id=prince.id, schema_id=plot_rel.id,
        value={"related_ids": [str(plot_with.id), str(plot_without.id)]},
    )
    db.commit()

    compute_all_for_entry(db, chars_db.id, prince.id)
    db.commit()

    pv = repo.get_value(db, prince.id, geburtsort_test.id)
    assert pv is not None
    # Resolved to relation chips, not raw descriptors / [object Object].
    assert pv.value.get("relation") is True
    result = pv.value["result"]
    assert isinstance(result, list)
    # Exactly ONE chip: Vyrenell. The empty-birthplace plot entry contributes
    # no placeholder chip.
    assert len(result) == 1
    assert result[0]["title"] == "Vyrenell"
    assert result[0]["id"] == str(vyrenell.id)


def test_rollup_over_timeline_relation_target_resolves_members(db, database_block, entry):
    """Regression: a rollup whose TARGET column is a timeline relation must
    resolve the related IDs from the last ``_timeline`` slot.

    Previously ``_compute_rollup`` called ``_extract_scalar`` without the target
    column's config, so a timeline relation target — whose ids live inside
    ``_timeline`` slots, not at the root ``related_ids`` — came back empty,
    while flat relation targets worked.
    """
    org = repo.create_block(db, type="page", position=2.0, parent_id=database_block.id)
    org.content = {"title": "ACME GmbH"}
    member = repo.create_block(db, type="page", position=3.0, parent_id=database_block.id)
    member.content = {"title": "Lyz"}
    db.commit()

    rel = repo.create_schema(
        db, database_id=database_block.id, name="Organisation", type="relation",
        position=1.0, config={"target_database_id": str(database_block.id)},
    )
    members = repo.create_schema(
        db, database_id=database_block.id, name="Members", type="relation",
        position=2.0,
        config={"target_database_id": str(database_block.id), "hasTimeline": True},
    )
    roll = repo.create_schema(
        db, database_id=database_block.id, name="Org members", type="rollup",
        position=3.0,
        config={
            "relation_schema_id": str(rel.id),
            "rollup_schema_id": str(members.id),
            "function": "show_original",
        },
    )
    db.commit()

    # char -> org (flat path relation)
    repo.upsert_value(
        db, page_id=entry.id, schema_id=rel.id,
        value={"related_ids": [str(org.id)]},
    )
    # org.Members -> member, stored as a timeline value (ids only inside the slot)
    repo.upsert_value(
        db, page_id=org.id, schema_id=members.id,
        value={
            "relationPool": {str(member.id): ["2024-01-01T00:00:00→"]},
            "_timeline": {"2024-01-01T00:00:00→": {"related_ids": [str(member.id)]}},
        },
    )
    db.commit()

    compute_all_for_entry(db, database_block.id, entry.id)
    db.commit()

    pv = repo.get_value(db, entry.id, roll.id)
    assert pv is not None
    result = pv.value.get("result")
    assert isinstance(result, list) and len(result) == 1
    assert result[0]["id"] == str(member.id)
    assert result[0]["title"] == "Lyz"
