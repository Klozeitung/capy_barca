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
    READS_TITLE,
    CycleError,
    SchemaLike,
    _aggregate,
    _extract_scalar,
    _infer_result_type,
    _relation_target_database_id,
    _resolve_formula_relation,
    _resolve_relation_entries,
    _serialise_formula_result,
    build_cross_read_map,
    build_dependency_graph,
    compute_all_for_entry,
    compute_cross_db_dependents,
    compute_same_db_rollup_dependents,
    formula_expression_with_ids,
    formula_expression_with_names,
    formula_reference_index,
    has_any_cycle,
    recompute_after_value_change,
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


def test_dependency_graph_survives_a_pathological_expression():
    """
    The graph builder catches FormulaError and nothing else, so the engine has
    to guarantee that is all it raises. A formula nested past the parser's cap
    used to escape as a RecursionError and surface as a 500 rather than as an
    unparseable formula with no dependencies.
    """
    schema_id = uuid.uuid4()
    schemas = [
        SchemaLike(
            id=schema_id,
            name="Pathological",
            type="formula",
            config={"expression": "(" * 400 + "1" + ")" * 400},
        )
    ]
    graph = build_dependency_graph(schemas)
    assert graph[schema_id] == set()


def test_dependency_graph_survives_an_over_long_operator_chain():
    """A chain past the operator-chain bound is unparseable, not a 500."""
    schema_id = uuid.uuid4()
    schemas = [
        SchemaLike(
            id=schema_id,
            name="TooLong",
            type="formula",
            config={"expression": "1" + "+1" * 5000},
        )
    ]
    assert build_dependency_graph(schemas)[schema_id] == set()


def test_dependency_graph_resolves_a_long_flat_expression():
    """Length alone no longer stops the graph from seeing a formula's deps."""
    price = SchemaLike(id=uuid.uuid4(), name="Price", type="number", config=None)
    branches = ", ".join(f"prop('Price') == {i}, {i}" for i in range(300))
    formula = SchemaLike(
        id=uuid.uuid4(), name="Bucket", type="formula",
        config={"expression": f"ifs({branches}, -1)"},
    )
    graph = build_dependency_graph([price, formula])
    assert graph[formula.id] == {price.id}


def test_infer_result_type_date_range_dict():
    # A formula that yields a date property directly (prop('date')) returns a
    # {"start", "end"} dict; it must be classified as "date" so the filter UI
    # offers date operators for the column.
    assert _infer_result_type({"start": "2000-01-01T00:01", "end": "2000-01-02T00:01"}) == "date"


def test_infer_result_type_date_range_dict_start_only():
    assert _infer_result_type({"start": "2000-01-01", "end": None}) == "date"


def test_infer_result_type_relation_descriptor_dict_is_not_date():
    # A resolved relation descriptor is also a dict but carries no start/end;
    # it must not be mistaken for a date result.
    descriptor = {"id": "u1", "title": "City X", "database_id": "db"}
    assert _infer_result_type(descriptor) == "text"


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
    assert _extract_scalar("select", {"option": "A · Value Block 1"}) == "A · Value Block 1"


def test_extract_scalar_select_option_key_empty_string():
    # Empty option (no selection) must return "" not fall through to "selected"
    assert _extract_scalar("select", {"option": ""}) == ""


def test_extract_scalar_select_option_key_takes_priority():
    # If both keys are present, "option" wins
    assert _extract_scalar("select", {"option": "A", "selected": "B"}) == "A"


def test_extract_scalar_select_options_key_multi():
    # Multi-select frontend format: {"options": ["label", …]} — first label
    # is returned so prop('Kind') == "Value A" works for the single-value case.
    assert _extract_scalar("select", {"options": ["Value A"]}) == "Value A"


def test_extract_scalar_select_options_key_multiple_values():
    assert _extract_scalar("select", {"options": ["Value A", "Value B"]}) == "Value A"


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
            {"id": "u1", "title": "City X", "database_id": "db"},
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


# ── _resolve_relation_entries (relation rollup → clickable chips) ─────────────

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


def test_graph_formula_resolves_id_references():
    price = _schema("Price", "number")
    total = _schema("Total", "formula", {"expression": f"prop('{price.id}') * 2"})
    graph = build_dependency_graph([price, total])
    assert graph[total.id] == {price.id}


def test_graph_formula_no_self_reference_by_id_in_deps():
    s = _schema("X", "formula", {"expression": "1"})
    s.config = {"expression": f"prop('{s.id}') + 1"}
    assert s.id not in build_dependency_graph([s])[s.id]


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


# ─── Formula references: names and ids ────────────────────────────────────────


def test_reference_index_resolves_names_and_ids():
    price = _schema("Price", "number")
    index = formula_reference_index([price])
    assert index["Price"] is price
    assert index[str(price.id)] is price


def test_reference_index_lets_an_id_win_over_a_name_spelled_like_it():
    price = _schema("Price", "number")
    impostor = _schema(str(price.id), "text")
    assert formula_reference_index([price, impostor])[str(price.id)] is price


def test_expression_with_ids_and_back_with_names():
    price = _schema("Price", "number")
    qty = _schema("Qty", "number")
    schemas = [price, qty]
    stored = formula_expression_with_ids("prop('Price') * prop(\"Qty\") + prop('Gone')", schemas)
    assert stored == f"prop('{price.id}') * prop(\"{qty.id}\") + prop('Gone')"
    assert formula_expression_with_names(stored, schemas) == (
        "prop('Price') * prop(\"Qty\") + prop('Gone')"
    )


def test_expression_with_ids_reads_a_name_spelled_like_an_id_as_that_id():
    price = _schema("Price", "number")
    impostor = _schema(str(price.id), "text")
    stored = f"prop('{price.id}')"
    assert formula_expression_with_ids(stored, [price, impostor]) == stored


def test_expression_with_ids_leaves_other_strings_alone():
    status = _schema("Status", "text")
    expression = "if(prop('Status') == 'Status', 'a', 'b')"
    assert formula_expression_with_ids(expression, [status]) == (
        f"if(prop('{status.id}') == 'Status', 'a', 'b')"
    )


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


def test_formula_reads_properties_by_id_and_by_name(db, database_block, entry):
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
        position=3.0, config={"expression": f"prop('{price.id}') * prop('Qty')"},
    )
    db.commit()
    repo.upsert_value(db, page_id=entry.id, schema_id=price.id, value={"number": 4})
    repo.upsert_value(db, page_id=entry.id, schema_id=qty.id, value={"number": 3})
    db.commit()

    compute_all_for_entry(db, database_block.id, entry.id)
    db.commit()

    assert repo.get_value(db, entry.id, total.id).value["result"] == pytest.approx(12.0)


def test_formula_by_id_survives_a_rename(db, database_block, entry):
    price = repo.create_schema(
        db, database_id=database_block.id, name="Price", type="number",
        position=1.0, config={"format": "plain"},
    )
    total = repo.create_schema(
        db, database_id=database_block.id, name="Total", type="formula",
        position=2.0, config={"expression": f"prop('{price.id}') + 1"},
    )
    db.commit()
    repo.upsert_value(db, page_id=entry.id, schema_id=price.id, value={"number": 4})
    repo.update_schema(db, price, name="Cost")
    db.commit()

    compute_all_for_entry(db, database_block.id, entry.id)
    db.commit()

    assert repo.get_value(db, entry.id, total.id).value["result"] == pytest.approx(5.0)


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


# _extract_scalar: timeline-aware value-bearing scalars

def test_extract_scalar_text_timeline_last_slot():
    # A timelined text column stores its payload inside _timeline slots; the
    # last (chronologically newest) slot must be surfaced, not None.
    val = {"_timeline": {
        "→1999-12-31T23:59:59": {"text": "Test 1"},
        "2000-01-01T00:00:00→": {"text": "Test 2"},
    }}
    assert _extract_scalar("text", val) == "Test 2"


def test_extract_scalar_text_timeline_always_valid_slot():
    # The always-valid singleton ("" key) is the sole, current slot.
    val = {"_timeline": {"": {"text": "Test 3"}}}
    assert _extract_scalar("text", val) == "Test 3"


def test_extract_scalar_text_timeline_empty():
    # An empty _timeline resolves to no slot -> None.
    assert _extract_scalar("text", {"_timeline": {}}) is None


def test_extract_scalar_text_flat_empty_string_preserved():
    # Regression guard for the "text" key fallback: an explicit empty string
    # must stay "" and never fall through to "value".
    assert _extract_scalar("text", {"text": ""}) == ""


def test_extract_scalar_email_timeline_value_key():
    # Timelined email/phone/url slots store the payload under "value".
    val = {"_timeline": {"": {"value": "a@b.c"}}}
    assert _extract_scalar("email", val) == "a@b.c"


def test_extract_scalar_number_timeline_last_slot():
    val = {"_timeline": {
        "2023-01-01T00:00:00→2023-12-31T23:59:59": {"number": 99},
        "2024-01-01T00:00:00→": {"number": 5},
    }}
    assert _extract_scalar("number", val) == 5


def test_extract_scalar_checkbox_timeline_last_slot():
    val = {"_timeline": {"": {"checked": True}}}
    assert _extract_scalar("checkbox", val) is True


def test_extract_scalar_select_timeline_last_slot():
    val = {"_timeline": {"": {"option": "Value A"}}}
    assert _extract_scalar("select", val) == "Value A"


def test_extract_scalar_date_timeline_last_slot():
    val = {"_timeline": {"": {"start": "2025-01-01", "end": "2025-01-31"}}}
    assert _extract_scalar("date", val) == {"start": "2025-01-01", "end": "2025-01-31"}


# _extract_scalar: parent_item / sub_item (hierarchy mirrors, related-id shape)

def test_extract_scalar_parent_item_returns_ids():
    assert _extract_scalar("parent_item", {"related_ids": ["p1"]}) == ["p1"]


def test_extract_scalar_sub_item_returns_ids():
    assert _extract_scalar("sub_item", {"related_ids": ["c1", "c2"]}) == ["c1", "c2"]


def test_extract_scalar_parent_item_empty():
    assert _extract_scalar("parent_item", {"related_ids": []}) == []


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


# ─── compute_all_for_entry: formula returning relation prop ───────────────────


def test_formula_relation_prop_resolves_to_chips(db, database_block, entry):
    """A formula that returns prop('Rel') yields resolved chips with relation=True."""
    # Create a second entry that will be the relation target
    target = repo.create_block(db, type="page", position=2.0, parent_id=database_block.id)
    target.content = {"title": "Target Entry"}
    target.icon = "mdi:star-outline"
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
    assert pv.value["result"][0]["icon"] == "mdi:star-outline"
    assert "error" not in pv.value


@pytest.mark.parametrize("function, expected", [
    ("show_original", [{"icon": "mdi:star-outline"}, {"icon": None}]),
    ("first_value", {"icon": "mdi:star-outline"}),
])
def test_rollup_over_a_relation_carries_the_icons_of_the_linked_entries(
    db, database_block, entry, function, expected,
):
    """Rollup chips show the icon of each entry, None for one without its own."""
    middle = repo.create_block(db, type="page", position=2.0, parent_id=database_block.id)
    with_icon = repo.create_block(db, type="page", position=3.0, parent_id=database_block.id)
    with_icon.content = {"title": "Entry A"}
    with_icon.icon = "mdi:star-outline"
    without_icon = repo.create_block(db, type="page", position=4.0, parent_id=database_block.id)
    without_icon.content = {"title": "Entry B"}
    first = repo.create_schema(
        db, database_id=database_block.id, name="First", type="relation",
        position=1.0, config={"target_database_id": str(database_block.id)},
    )
    second = repo.create_schema(
        db, database_id=database_block.id, name="Second", type="relation",
        position=2.0, config={"target_database_id": str(database_block.id)},
    )
    rollup = repo.create_schema(
        db, database_id=database_block.id, name="Through", type="rollup", position=3.0,
        config={
            "relation_schema_id": str(first.id),
            "rollup_schema_id": str(second.id),
            "function": function,
        },
    )
    repo.upsert_value(
        db, page_id=entry.id, schema_id=first.id, value={"related_ids": [str(middle.id)]},
    )
    repo.upsert_value(
        db, page_id=middle.id, schema_id=second.id,
        value={"related_ids": [str(with_icon.id), str(without_icon.id)]},
    )
    db.commit()

    compute_all_for_entry(db, database_block.id, entry.id)
    db.commit()

    result = repo.get_value(db, entry.id, rollup.id).value["result"]
    if isinstance(expected, list):
        assert [{"icon": chip["icon"]} for chip in result] == expected
    else:
        assert {"icon": result["icon"]} == expected


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
        if(prop('Kind') == "Value A", prop('Place'), "")
    where 'Kind' is a multi-select storing {"options": ["Value A"]}.
    """
    target = repo.create_block(db, type="page", position=2.0, parent_id=database_block.id)
    target.content = {"title": "City X"}
    db.commit()

    kind = repo.create_schema(
        db, database_id=database_block.id, name="Kind", type="select",
        position=1.0, config={"mode": "multiple", "options": [{"label": "Value A"}]},
    )
    place = repo.create_schema(
        db, database_id=database_block.id, name="Place", type="relation",
        position=2.0, config={"target_database_id": str(database_block.id)},
    )
    formula = repo.create_schema(
        db, database_id=database_block.id, name="Formula", type="formula",
        position=3.0, config={"expression": "if(prop('Kind')==\"Value A\",prop('Place'),\"\")"},
    )
    db.commit()

    repo.upsert_value(db, page_id=entry.id, schema_id=kind.id, value={"options": ["Value A"]})
    repo.upsert_value(
        db, page_id=entry.id, schema_id=place.id,
        value={"related_ids": [str(target.id)]},
    )
    db.commit()

    compute_all_for_entry(db, database_block.id, entry.id)
    db.commit()

    pv = repo.get_value(db, entry.id, formula.id)
    assert pv is not None
    assert pv.value.get("relation") is True
    assert len(pv.value["result"]) == 1
    assert pv.value["result"][0]["title"] == "City X"


def test_formula_multiselect_condition_not_met_stays_empty(db, database_block, entry):
    """When the multi-select value does not match, the else branch ("") wins."""
    target = repo.create_block(db, type="page", position=2.0, parent_id=database_block.id)
    target.content = {"title": "City X"}
    db.commit()

    kind = repo.create_schema(
        db, database_id=database_block.id, name="Kind", type="select",
        position=1.0, config={"mode": "multiple", "options": [{"label": "Value B"}]},
    )
    place = repo.create_schema(
        db, database_id=database_block.id, name="Place", type="relation",
        position=2.0, config={"target_database_id": str(database_block.id)},
    )
    formula = repo.create_schema(
        db, database_id=database_block.id, name="Formula", type="formula",
        position=3.0, config={"expression": "if(prop('Kind')==\"Value A\",prop('Place'),\"\")"},
    )
    db.commit()

    repo.upsert_value(db, page_id=entry.id, schema_id=kind.id, value={"options": ["Value B"]})
    repo.upsert_value(
        db, page_id=entry.id, schema_id=place.id,
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
      Source DB      : entries with an 'Place' relation and a formula
                       'Place Formula' = prop('Place')
      Reader DB      : 'Source' relation -> source entries
                       'Place Rollup' rollup (show_original) over the
                       source formula column.
    """
    # ── Target DB that 'Place' points at ─────────────────────────────────────
    places_db = repo.create_block(db, type="database", position=1.0, parent_id=workspace.id)
    db.commit()
    city_x = repo.create_block(db, type="page", position=1.0, parent_id=places_db.id)
    city_x.content = {"title": "City X"}
    db.commit()

    # ── Source DB: Place relation + formula returning prop('Place') ───────────
    source_db = repo.create_block(db, type="database", position=2.0, parent_id=workspace.id)
    db.commit()
    place = repo.create_schema(
        db, database_id=source_db.id, name="Place", type="relation",
        position=1.0, config={"target_database_id": str(places_db.id)},
    )
    place_formula = repo.create_schema(
        db, database_id=source_db.id, name="Place Formula", type="formula",
        position=2.0, config={"expression": "prop('Place')"},
    )
    db.commit()

    # Source entry WITH a place
    source_with = repo.create_block(db, type="page", position=1.0, parent_id=source_db.id)
    source_with.content = {"title": "Event Person A"}
    db.commit()
    repo.upsert_value(
        db, page_id=source_with.id, schema_id=place.id,
        value={"related_ids": [str(city_x.id)]},
    )
    # Source entry WITHOUT a place (empty Place)
    source_without = repo.create_block(db, type="page", position=2.0, parent_id=source_db.id)
    source_without.content = {"title": "Person A Test"}
    db.commit()
    repo.upsert_value(
        db, page_id=source_without.id, schema_id=place.id, value={"related_ids": []},
    )
    db.commit()

    compute_all_for_entry(db, source_db.id, source_with.id)
    compute_all_for_entry(db, source_db.id, source_without.id)
    db.commit()

    # ── Reader DB: Source relation + rollup (show_original) over formula ──────
    reader_db = repo.create_block(db, type="database", position=3.0, parent_id=workspace.id)
    db.commit()
    source_rel = repo.create_schema(
        db, database_id=reader_db.id, name="Source", type="relation",
        position=1.0, config={"target_database_id": str(source_db.id)},
    )
    place_rollup = repo.create_schema(
        db, database_id=reader_db.id, name="Place Rollup", type="rollup",
        position=2.0, config={
            "relation_schema_id": str(source_rel.id),
            "rollup_schema_id": str(place_formula.id),
            "function": "show_original",
        },
    )
    db.commit()

    # Entry linked to BOTH source entries (one with, one without place)
    reader_entry = repo.create_block(db, type="page", position=1.0, parent_id=reader_db.id)
    reader_entry.content = {"title": "Person B"}
    db.commit()
    repo.upsert_value(
        db, page_id=reader_entry.id, schema_id=source_rel.id,
        value={"related_ids": [str(source_with.id), str(source_without.id)]},
    )
    db.commit()

    compute_all_for_entry(db, reader_db.id, reader_entry.id)
    db.commit()

    pv = repo.get_value(db, reader_entry.id, place_rollup.id)
    assert pv is not None
    # Resolved to relation chips, not raw descriptors / [object Object].
    assert pv.value.get("relation") is True
    result = pv.value["result"]
    assert isinstance(result, list)
    # Exactly ONE chip: City X. The source entry without a place contributes
    # no placeholder chip.
    assert len(result) == 1
    assert result[0]["title"] == "City X"
    assert result[0]["id"] == str(city_x.id)


def test_rollup_over_timeline_relation_target_resolves_members(db, database_block, entry):
    """Regression: a rollup whose TARGET column is a timeline relation must
    resolve the related IDs from the last ``_timeline`` slot.

    Previously ``_compute_rollup`` called ``_extract_scalar`` without the target
    column's config, so a timeline relation target — whose ids live inside
    ``_timeline`` slots, not at the root ``related_ids`` — came back empty,
    while flat relation targets worked.
    """
    org = repo.create_block(db, type="page", position=2.0, parent_id=database_block.id)
    org.content = {"title": "Organisation X"}
    member = repo.create_block(db, type="page", position=3.0, parent_id=database_block.id)
    member.content = {"title": "Person C"}
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
    assert result[0]["title"] == "Person C"


def test_rollup_over_timeline_text_show_original(db, database_block, entry):
    """Regression: show_original over a TIMELINED text column must surface
    the last-slot text, not the "—" placeholder.

    Timelined text stores its payload inside ``_timeline`` slots; previously
    ``_extract_scalar`` read only the root ``text`` key and returned None for
    every related entry, so the rollup rendered a dash per row.
    """
    target = repo.create_block(db, type="page", position=2.0, parent_id=database_block.id)
    target.content = {"title": "A1"}
    db.commit()

    rel = repo.create_schema(
        db, database_id=database_block.id, name="Rel", type="relation",
        position=1.0, config={"target_database_id": str(database_block.id)},
    )
    note = repo.create_schema(
        db, database_id=database_block.id, name="Note", type="text",
        position=2.0, config={"hasTimeline": True},
    )
    roll = repo.create_schema(
        db, database_id=database_block.id, name="Note rollup", type="rollup",
        position=3.0, config={
            "relation_schema_id": str(rel.id),
            "rollup_schema_id": str(note.id),
            "function": "show_original",
        },
    )
    db.commit()

    # entry -> target (flat relation)
    repo.upsert_value(
        db, page_id=entry.id, schema_id=rel.id,
        value={"related_ids": [str(target.id)]},
    )
    # target.Note: two timelined slots; the later slot ("Test 2") is last state.
    repo.upsert_value(
        db, page_id=target.id, schema_id=note.id,
        value={"_timeline": {
            "→1999-12-31T23:59:59": {"text": "Test 1"},
            "2000-01-01T00:00:00→": {"text": "Test 2"},
        }},
    )
    db.commit()

    compute_all_for_entry(db, database_block.id, entry.id)
    db.commit()

    pv = repo.get_value(db, entry.id, roll.id)
    assert pv is not None
    assert pv.value.get("result") == ["Test 2"]


def test_rollup_over_timeline_number_sum(db, database_block, entry):
    """A numeric aggregation (sum) over a TIMELINED number column must read the
    current last-slot value for every related entry, not None.

    Covers both the always-valid ("" key) slot and a bounded-then-open
    timeline, confirming the generalisation beyond text/show_original.
    """
    t1 = repo.create_block(db, type="page", position=2.0, parent_id=database_block.id)
    t1.content = {"title": "T1"}
    t2 = repo.create_block(db, type="page", position=3.0, parent_id=database_block.id)
    t2.content = {"title": "T2"}
    db.commit()

    rel = repo.create_schema(
        db, database_id=database_block.id, name="Rel", type="relation",
        position=1.0, config={"target_database_id": str(database_block.id)},
    )
    amount = repo.create_schema(
        db, database_id=database_block.id, name="Amount", type="number",
        position=2.0, config={"hasTimeline": True},
    )
    roll = repo.create_schema(
        db, database_id=database_block.id, name="Total", type="rollup",
        position=3.0, config={
            "relation_schema_id": str(rel.id),
            "rollup_schema_id": str(amount.id),
            "function": "sum",
        },
    )
    db.commit()

    repo.upsert_value(
        db, page_id=entry.id, schema_id=rel.id,
        value={"related_ids": [str(t1.id), str(t2.id)]},
    )
    repo.upsert_value(
        db, page_id=t1.id, schema_id=amount.id,
        value={"_timeline": {"": {"number": 10}}},
    )
    repo.upsert_value(
        db, page_id=t2.id, schema_id=amount.id,
        value={"_timeline": {
            "2023-01-01T00:00:00→2023-12-31T23:59:59": {"number": 99},
            "2024-01-01T00:00:00→": {"number": 5},
        }},
    )
    db.commit()

    compute_all_for_entry(db, database_block.id, entry.id)
    db.commit()

    pv = repo.get_value(db, entry.id, roll.id)
    assert pv is not None
    # 10 (always) + 5 (last slot, not 99) = 15
    assert pv.value.get("result") == 15.0


def test_rollup_over_date_show_original_emits_iso_string(db, database_block, entry):
    """show_original over a date column must emit a canonical ISO string, not
    the {"start", "end"} dict (which the frontend renders as "[object Object]").
    """
    target = repo.create_block(db, type="page", position=2.0, parent_id=database_block.id)
    target.content = {"title": "D1"}
    db.commit()

    rel = repo.create_schema(
        db, database_id=database_block.id, name="Rel", type="relation",
        position=1.0, config={"target_database_id": str(database_block.id)},
    )
    when = repo.create_schema(
        db, database_id=database_block.id, name="When", type="date",
        position=2.0, config={},
    )
    roll = repo.create_schema(
        db, database_id=database_block.id, name="When rollup", type="rollup",
        position=3.0, config={
            "relation_schema_id": str(rel.id),
            "rollup_schema_id": str(when.id),
            "function": "show_original",
        },
    )
    db.commit()

    repo.upsert_value(
        db, page_id=entry.id, schema_id=rel.id,
        value={"related_ids": [str(target.id)]},
    )
    repo.upsert_value(
        db, page_id=target.id, schema_id=when.id,
        value={"start": "2025-01-01", "end": None},
    )
    db.commit()

    compute_all_for_entry(db, database_block.id, entry.id)
    db.commit()

    pv = repo.get_value(db, entry.id, roll.id)
    assert pv is not None
    assert pv.value.get("result") == ["2025-01-01"]


# ─── Dependent recompute after a linked entry changes ─────────────────────────
#
# A formula that passes a relation through stores the linked entries' titles.
# When a linked entry changes, the entries that read it are recomputed; that
# has to include databases that reach the entry only through a formula, not
# just through a rollup.


def _rename(db, block, title: str) -> None:
    block.content = {"title": title}
    db.commit()


def _chip_titles(db, entry_id, schema_id) -> list[str]:
    pv = repo.get_value(db, entry_id, schema_id)
    return [chip["title"] for chip in pv.value["result"]]


def test_relation_target_of_a_plain_relation():
    target = uuid.uuid4()
    schema = SchemaLike(
        id=uuid.uuid4(), name="Rel", type="relation",
        config={"target_database_id": str(target)},
    )
    assert _relation_target_database_id(schema) == target


def test_relation_target_of_a_hierarchy_mirror_is_its_own_database():
    class _Hierarchy:
        type = "parent_item"
        config: dict = {}
        database_id = uuid.uuid4()

    assert _relation_target_database_id(_Hierarchy()) == _Hierarchy.database_id


def test_relation_target_missing_or_malformed_is_none():
    no_target = SchemaLike(id=uuid.uuid4(), name="R", type="relation", config={})
    bad_target = SchemaLike(
        id=uuid.uuid4(), name="R", type="relation",
        config={"target_database_id": "not-a-uuid"},
    )
    assert _relation_target_database_id(no_target) is None
    assert _relation_target_database_id(bad_target) is None


@pytest.fixture
def formula_only_link(db, workspace):
    """
    ``people`` reaches ``places`` only through a formula, no rollup at all.
    """
    places = repo.create_block(db, type="database", position=1.0, parent_id=workspace.id)
    place = repo.create_block(db, type="page", position=1.0, parent_id=places.id)
    place.content = {"title": "Old"}
    people = repo.create_block(db, type="database", position=2.0, parent_id=workspace.id)
    rel = repo.create_schema(
        db, database_id=people.id, name="Place", type="relation",
        position=1.0, config={"target_database_id": str(places.id)},
    )
    formula = repo.create_schema(
        db, database_id=people.id, name="Where", type="formula",
        position=2.0, config={"expression": "prop('Place')"},
    )
    linked = repo.create_block(db, type="page", position=1.0, parent_id=people.id)
    unlinked = repo.create_block(db, type="page", position=2.0, parent_id=people.id)
    repo.upsert_value(
        db, page_id=linked.id, schema_id=rel.id,
        value={"related_ids": [str(place.id)]},
    )
    db.commit()
    compute_all_for_entry(db, people.id, linked.id)
    compute_all_for_entry(db, people.id, unlinked.id)
    db.commit()
    return places, place, people, linked, unlinked, formula


def test_cross_db_cascade_finds_formula_only_dependents(db, formula_only_link):
    places, place, people, linked, unlinked, formula = formula_only_link
    _rename(db, place, "New")

    affected = compute_cross_db_dependents(db, places.id, place.id)
    db.commit()

    assert affected == [people.id]
    assert _chip_titles(db, linked.id, formula.id) == ["New"]


def test_cross_db_cascade_skips_entries_not_linking_to_the_change(db, formula_only_link):
    places, place, people, linked, unlinked, formula = formula_only_link
    before = repo.get_value(db, unlinked.id, formula.id).value
    _rename(db, place, "New")

    compute_cross_db_dependents(db, places.id, place.id)
    db.commit()

    assert repo.get_value(db, unlinked.id, formula.id).value == before


def test_cross_db_cascade_ignores_formulas_without_a_relation(db, workspace):
    places = repo.create_block(db, type="database", position=1.0, parent_id=workspace.id)
    place = repo.create_block(db, type="page", position=1.0, parent_id=places.id)
    other = repo.create_block(db, type="database", position=2.0, parent_id=workspace.id)
    repo.create_schema(
        db, database_id=other.id, name="Calc", type="formula",
        position=1.0, config={"expression": "1 + 1"},
    )
    db.commit()

    assert compute_cross_db_dependents(db, places.id, place.id) == []


def test_cross_db_cascade_still_finds_rollup_dependents(db, workspace):
    places = repo.create_block(db, type="database", position=1.0, parent_id=workspace.id)
    place = repo.create_block(db, type="page", position=1.0, parent_id=places.id)
    place.content = {"title": "Old"}
    people = repo.create_block(db, type="database", position=2.0, parent_id=workspace.id)
    rel = repo.create_schema(
        db, database_id=people.id, name="Place", type="relation",
        position=1.0, config={"target_database_id": str(places.id)},
    )
    region = repo.create_schema(
        db, database_id=places.id, name="Region", type="text", position=1.0, config=None,
    )
    roll = repo.create_schema(
        db, database_id=people.id, name="Regions", type="rollup", position=2.0,
        config={
            "relation_schema_id": str(rel.id),
            "rollup_schema_id": str(region.id),
            "function": "show_original",
        },
    )
    repo.upsert_value(db, page_id=place.id, schema_id=region.id, value={"text": "North"})
    person = repo.create_block(db, type="page", position=1.0, parent_id=people.id)
    repo.upsert_value(
        db, page_id=person.id, schema_id=rel.id,
        value={"related_ids": [str(place.id)]},
    )
    db.commit()
    compute_all_for_entry(db, people.id, person.id)
    db.commit()
    assert repo.get_value(db, person.id, roll.id).value["result"] == ["North"]
    repo.upsert_value(db, page_id=place.id, schema_id=region.id, value={"text": "South"})
    db.commit()

    assert compute_cross_db_dependents(db, places.id, place.id) == [people.id]
    db.commit()
    assert repo.get_value(db, person.id, roll.id).value["result"] == ["South"]


def test_same_db_cascade_finds_formula_dependents(db, database_block):
    target = repo.create_block(db, type="page", position=1.0, parent_id=database_block.id)
    target.content = {"title": "Old"}
    rel = repo.create_schema(
        db, database_id=database_block.id, name="Rel", type="relation",
        position=1.0, config={"target_database_id": str(database_block.id)},
    )
    formula = repo.create_schema(
        db, database_id=database_block.id, name="RelFormula", type="formula",
        position=2.0, config={"expression": "prop('Rel')"},
    )
    source = repo.create_block(db, type="page", position=2.0, parent_id=database_block.id)
    repo.upsert_value(
        db, page_id=source.id, schema_id=rel.id,
        value={"related_ids": [str(target.id)]},
    )
    db.commit()
    compute_all_for_entry(db, database_block.id, source.id)
    db.commit()
    _rename(db, target, "New")

    assert compute_same_db_rollup_dependents(db, database_block.id, target.id) is True
    db.commit()
    assert _chip_titles(db, source.id, formula.id) == ["New"]


def test_same_db_cascade_without_dependents_reports_nothing(db, database_block, entry):
    assert compute_same_db_rollup_dependents(db, database_block.id, entry.id) is False


# ─── Finding dependents without scanning every entry ─────────────────────────
#
# The cascade runs after every relation write. Looking up the entries that link
# to the changed one used to cost one query per entry of every reading
# database, which made a single write take seconds on a large database.


def _reader_with_entries(db, workspace, unlinked: int):
    """
    ``readers`` has a formula over a relation into ``places``; one entry links
    to ``place``, *unlinked* others link elsewhere.
    """
    places = repo.create_block(db, type="database", position=1.0, parent_id=workspace.id)
    place = repo.create_block(db, type="page", position=1.0, parent_id=places.id)
    elsewhere = repo.create_block(db, type="page", position=2.0, parent_id=places.id)
    readers = repo.create_block(db, type="database", position=2.0, parent_id=workspace.id)
    rel = repo.create_schema(
        db, database_id=readers.id, name="Place", type="relation",
        position=1.0, config={"target_database_id": str(places.id)},
    )
    repo.create_schema(
        db, database_id=readers.id, name="Where", type="formula",
        position=2.0, config={"expression": "prop('Place')"},
    )
    linked = repo.create_block(db, type="page", position=1.0, parent_id=readers.id)
    repo.upsert_value(
        db, page_id=linked.id, schema_id=rel.id, value={"related_ids": [str(place.id)]},
    )
    for i in range(unlinked):
        other = repo.create_block(db, type="page", position=2.0 + i, parent_id=readers.id)
        repo.upsert_value(
            db, page_id=other.id, schema_id=rel.id,
            value={"related_ids": [str(elsewhere.id)]},
        )
    db.commit()
    return places, place, readers, linked, rel


def test_cascade_query_count_does_not_grow_with_the_reading_database(
    db, workspace, count_queries,
):
    places, place, readers, _, rel = _reader_with_entries(db, workspace, unlinked=3)
    places_id, place_id, readers_id, rel_id = places.id, place.id, readers.id, rel.id
    # A first run stores the computed values, so both measured runs find
    # them unchanged.
    compute_cross_db_dependents(db, places_id, place_id)
    db.commit()
    db.expunge_all()
    with count_queries() as small:
        compute_cross_db_dependents(db, places_id, place_id)
    db.commit()

    elsewhere = repo.create_block(db, type="page", position=3.0, parent_id=places_id)
    for i in range(40):
        other = repo.create_block(db, type="page", position=100.0 + i, parent_id=readers_id)
        repo.upsert_value(
            db, page_id=other.id, schema_id=rel_id,
            value={"related_ids": [str(elsewhere.id)]},
        )
    db.commit()
    db.expunge_all()
    with count_queries() as large:
        compute_cross_db_dependents(db, places_id, place_id)
    db.commit()

    assert large.count == small.count


def test_links_to_finds_timeline_and_ignores_mere_mentions(db, workspace):
    from app.blocks.computed import _links_to

    places, place, readers, linked, rel = _reader_with_entries(db, workspace, unlinked=0)
    in_timeline = repo.create_block(db, type="page", position=5.0, parent_id=readers.id)
    repo.upsert_value(
        db, page_id=in_timeline.id, schema_id=rel.id,
        value={"_timeline": {"2000-07-28T00:00:00→": {"related_ids": [str(place.id)]}}},
    )
    # The id occurs in the text of the value, but not as a linked entry.
    mention_only = repo.create_block(db, type="page", position=6.0, parent_id=readers.id)
    repo.upsert_value(
        db, page_id=mention_only.id, schema_id=rel.id,
        value={"related_ids": [], "nuances": {str(place.id): "Value A"}},
    )
    trashed = repo.create_block(db, type="page", position=7.0, parent_id=readers.id)
    repo.upsert_value(
        db, page_id=trashed.id, schema_id=rel.id, value={"related_ids": [str(place.id)]},
    )
    trashed.state = "trash"
    db.commit()

    found = _links_to(db, readers.id, {rel.id}, place.id)

    assert list(found) == [linked.id, in_timeline.id]
    assert found[linked.id] == {rel.id}


def test_links_to_without_relations_asks_nothing(db, database_block, count_queries):
    from app.blocks.computed import _links_to

    database_id = database_block.id
    with count_queries() as counter:
        assert _links_to(db, database_id, set(), uuid.uuid4()) == {}
    assert counter.count == 0


# ─── Writing only what changed ───────────────────────────────────────────────


def _doubling_formula(db, database_block, entry):
    base = repo.create_schema(
        db, database_id=database_block.id, name="Base", type="number",
        position=1.0, config=None,
    )
    doubled = repo.create_schema(
        db, database_id=database_block.id, name="Doubled", type="formula",
        position=2.0, config={"expression": "prop('Base') * 2"},
    )
    repo.upsert_value(db, page_id=entry.id, schema_id=base.id, value={"number": 5})
    db.commit()
    return base, doubled


def test_compute_all_reports_the_schemas_it_changed(db, database_block, entry):
    base, doubled = _doubling_formula(db, database_block, entry)

    assert compute_all_for_entry(db, database_block.id, entry.id) == {doubled.id}
    db.commit()
    assert compute_all_for_entry(db, database_block.id, entry.id) == set()

    repo.upsert_value(db, page_id=entry.id, schema_id=base.id, value={"number": 6})
    db.commit()
    assert compute_all_for_entry(db, database_block.id, entry.id) == {doubled.id}


def test_compute_all_writes_nothing_when_no_result_changed(
    db, database_block, entry, count_queries,
):
    _doubling_formula(db, database_block, entry)
    database_id, entry_id = database_block.id, entry.id
    compute_all_for_entry(db, database_id, entry_id)
    db.commit()

    with count_queries() as counter:
        compute_all_for_entry(db, database_id, entry_id)
        db.flush()

    assert not any(sql.lstrip().upper().startswith(("UPDATE", "INSERT")) for sql in counter.statements)


# ─── Read map across databases ───────────────────────────────────────────────


def test_read_map_names_what_rollups_and_formulas_read_through_a_relation(db):
    source_id, other_id = uuid.uuid4(), uuid.uuid4()
    column = uuid.uuid4()
    rel = _schema("Link", "relation", {"target_database_id": str(source_id)})
    elsewhere = _schema("Elsewhere", "relation", {"target_database_id": str(other_id)})
    schemas = [
        rel,
        elsewhere,
        _schema("Sum", "rollup", {
            "relation_schema_id": str(rel.id), "rollup_schema_id": str(column),
            "function": "sum",
        }),
        _schema("Titles", "formula", {"expression": "prop('Link')"}),
        _schema("Other", "rollup", {
            "relation_schema_id": str(elsewhere.id), "rollup_schema_id": str(uuid.uuid4()),
            "function": "count",
        }),
    ]

    assert build_cross_read_map(db, schemas, source_id) == {rel.id: {column, READS_TITLE}}


def test_read_map_keeps_a_relation_read_by_an_unfinished_rollup(db):
    source_id = uuid.uuid4()
    rel = _schema("Link", "relation", {"target_database_id": str(source_id)})
    unfinished = _schema("Count", "rollup", {"relation_schema_id": str(rel.id)})

    assert build_cross_read_map(db, [rel, unfinished], source_id) == {rel.id: set()}


@pytest.fixture
def rollup_reader(db, workspace):
    """
    ``readers`` rolls up the ``Region`` column of ``places`` through ``Place``;
    ``Size`` is a column of ``places`` that nothing reads.
    """
    places = repo.create_block(db, type="database", position=1.0, parent_id=workspace.id)
    place = repo.create_block(db, type="page", position=1.0, parent_id=places.id)
    region = repo.create_schema(
        db, database_id=places.id, name="Region", type="text", position=1.0, config=None,
    )
    size = repo.create_schema(
        db, database_id=places.id, name="Size", type="number", position=2.0, config=None,
    )
    readers = repo.create_block(db, type="database", position=2.0, parent_id=workspace.id)
    rel = repo.create_schema(
        db, database_id=readers.id, name="Place", type="relation",
        position=1.0, config={"target_database_id": str(places.id)},
    )
    roll = repo.create_schema(
        db, database_id=readers.id, name="Regions", type="rollup", position=2.0,
        config={
            "relation_schema_id": str(rel.id),
            "rollup_schema_id": str(region.id),
            "function": "show_original",
        },
    )
    reader = repo.create_block(db, type="page", position=1.0, parent_id=readers.id)
    repo.upsert_value(db, page_id=place.id, schema_id=region.id, value={"text": "Value A"})
    repo.upsert_value(
        db, page_id=reader.id, schema_id=rel.id, value={"related_ids": [str(place.id)]},
    )
    db.commit()
    compute_all_for_entry(db, readers.id, reader.id)
    db.commit()
    return places, place, region, size, readers, reader, roll


def test_relations_read_by_computed_narrows_to_changed_columns(db, rollup_reader):
    from app.blocks.computed import _relations_read_by_computed

    places, _, region, size, readers, _, _ = rollup_reader
    schemas = repo.list_schemas(db, readers.id)
    rel_ids = {s.id for s in schemas if s.type == "relation"}

    assert _relations_read_by_computed(db, schemas, places.id) == rel_ids
    assert _relations_read_by_computed(db, schemas, places.id, {region.id}) == rel_ids
    assert _relations_read_by_computed(db, schemas, places.id, {size.id}) == set()


def test_change_of_an_unread_column_leaves_readers_alone(db, rollup_reader):
    places, place, _, size, readers, reader, roll = rollup_reader
    repo.upsert_value(db, page_id=place.id, schema_id=size.id, value={"number": 3})
    db.commit()

    assert recompute_after_value_change(db, places.id, place.id, {size.id}) == []
    db.commit()
    assert repo.get_value(db, reader.id, roll.id).value["result"] == ["Value A"]


def test_change_of_a_read_column_reaches_the_reader(db, rollup_reader):
    places, place, region, _, readers, reader, roll = rollup_reader
    repo.upsert_value(db, page_id=place.id, schema_id=region.id, value={"text": "Value B"})
    db.commit()

    assert recompute_after_value_change(db, places.id, place.id, {region.id}) == [readers.id]
    db.commit()
    assert repo.get_value(db, reader.id, roll.id).value["result"] == ["Value B"]


def test_a_changed_computed_column_reaches_its_readers(db, workspace):
    """
    The reader rolls up a formula of the changed entry, not the written
    property: the formula's change is what has to propagate.
    """
    places = repo.create_block(db, type="database", position=1.0, parent_id=workspace.id)
    place = repo.create_block(db, type="page", position=1.0, parent_id=places.id)
    base = repo.create_schema(
        db, database_id=places.id, name="Base", type="number", position=1.0, config=None,
    )
    doubled = repo.create_schema(
        db, database_id=places.id, name="Doubled", type="formula",
        position=2.0, config={"expression": "prop('Base') * 2"},
    )
    readers = repo.create_block(db, type="database", position=2.0, parent_id=workspace.id)
    rel = repo.create_schema(
        db, database_id=readers.id, name="Place", type="relation",
        position=1.0, config={"target_database_id": str(places.id)},
    )
    roll = repo.create_schema(
        db, database_id=readers.id, name="Total", type="rollup", position=2.0,
        config={
            "relation_schema_id": str(rel.id),
            "rollup_schema_id": str(doubled.id),
            "function": "sum",
        },
    )
    reader = repo.create_block(db, type="page", position=1.0, parent_id=readers.id)
    repo.upsert_value(
        db, page_id=reader.id, schema_id=rel.id, value={"related_ids": [str(place.id)]},
    )
    repo.upsert_value(db, page_id=place.id, schema_id=base.id, value={"number": 4})
    db.commit()

    assert recompute_after_value_change(db, places.id, place.id, {base.id}) == [readers.id]
    db.commit()
    assert repo.get_value(db, reader.id, roll.id).value["result"] == pytest.approx(8.0)


def test_title_change_reaches_formula_readers(db, formula_only_link):
    places, place, people, linked, _, formula = formula_only_link
    _rename(db, place, "New")

    assert recompute_after_value_change(db, places.id, place.id, {READS_TITLE}) == [people.id]
    db.commit()
    assert _chip_titles(db, linked.id, formula.id) == ["New"]


def test_recompute_after_value_change_ignores_what_is_no_active_entry(db, database_block, entry):
    elsewhere = repo.create_block(db, type="database", position=2.0, parent_id=database_block.parent_id)
    trashed = repo.create_block(db, type="page", position=2.0, parent_id=database_block.id)
    trashed.state = "trash"
    db.commit()

    assert recompute_after_value_change(db, elsewhere.id, entry.id, {uuid.uuid4()}) == []
    assert recompute_after_value_change(db, database_block.id, trashed.id, {uuid.uuid4()}) == []
    assert recompute_after_value_change(db, database_block.id, uuid.uuid4(), {uuid.uuid4()}) == []


# ─── Option ids ───────────────────────────────────────────────────────────────


def test_extract_select_turns_option_ids_into_labels():
    config = {"options": [{"id": "o1", "label": "Value A"}, {"id": "o2", "label": "Value B"}]}
    assert _extract_scalar("select", {"option": "o2"}, config) == "Value B"
    assert _extract_scalar("select", {"options": ["o1", "o2"]}, config) == "Value A"
    assert _extract_scalar("select", {"_timeline": {"": {"option": "o1"}}}, config) == "Value A"


def test_extract_select_passes_unknown_ids_through():
    config = {"options": [{"id": "o1", "label": "Value A"}]}
    assert _extract_scalar("select", {"option": "o9"}, config) == "o9"


# ─── Evaluating only what a change reaches ────────────────────────────────────
#
# A sentinel stored in a computed value shows whether the pass touched it: a
# schema that was evaluated holds its real result afterwards, one that was
# skipped still holds the sentinel.

_SENTINEL = {"result": "untouched"}


def _plant(db, entry_id, *schemas) -> None:
    for schema in schemas:
        repo.upsert_value(db, page_id=entry_id, schema_id=schema.id, value=dict(_SENTINEL))
    db.commit()


def _evaluated(db, entry_id, schema) -> bool:
    return repo.get_value(db, entry_id, schema.id).value != _SENTINEL


@pytest.fixture
def two_chains(db, database_block, entry):
    """
    ``A`` feeds ``Doubled``, which feeds ``Quadrupled``; ``B`` feeds
    ``Other`` only.
    """
    a = repo.create_schema(db, database_id=database_block.id, name="A", type="number", position=1.0)
    b = repo.create_schema(db, database_id=database_block.id, name="B", type="number", position=2.0)
    doubled = repo.create_schema(
        db, database_id=database_block.id, name="Doubled", type="formula",
        position=3.0, config={"expression": "prop('A') * 2"},
    )
    quadrupled = repo.create_schema(
        db, database_id=database_block.id, name="Quadrupled", type="formula",
        position=4.0, config={"expression": "prop('Doubled') * 2"},
    )
    other = repo.create_schema(
        db, database_id=database_block.id, name="Other", type="formula",
        position=5.0, config={"expression": "prop('B') + 1"},
    )
    repo.upsert_value(db, page_id=entry.id, schema_id=a.id, value={"number": 3})
    repo.upsert_value(db, page_id=entry.id, schema_id=b.id, value={"number": 7})
    db.commit()
    return a, b, doubled, quadrupled, other


def test_compute_for_entry_evaluates_readers_of_the_change_transitively(
    db, database_block, entry, two_chains,
):
    from app.blocks.computed import compute_for_entry

    a, _, doubled, quadrupled, other = two_chains
    _plant(db, entry.id, doubled, quadrupled, other)

    changed = compute_for_entry(db, database_block.id, entry.id, {a.id})
    db.commit()

    assert changed == {doubled.id, quadrupled.id}
    assert repo.get_value(db, entry.id, quadrupled.id).value["result"] == 12
    assert not _evaluated(db, entry.id, other)


def test_compute_for_entry_stops_where_a_result_did_not_change(
    db, database_block, entry, two_chains,
):
    from app.blocks.computed import compute_for_entry

    a, _, doubled, quadrupled, _ = two_chains
    compute_all_for_entry(db, database_block.id, entry.id)
    db.commit()
    _plant(db, entry.id, quadrupled)

    # Doubled is evaluated and comes out the same, so Quadrupled is not.
    assert compute_for_entry(db, database_block.id, entry.id, {a.id}) == set()
    db.commit()
    assert not _evaluated(db, entry.id, quadrupled)


def test_compute_for_entry_evaluates_a_computed_schema_named_as_dirty(
    db, database_block, entry, two_chains,
):
    from app.blocks.computed import compute_for_entry

    _, _, doubled, quadrupled, other = two_chains
    _plant(db, entry.id, doubled, quadrupled, other)

    compute_for_entry(db, database_block.id, entry.id, {other.id})
    db.commit()

    assert repo.get_value(db, entry.id, other.id).value["result"] == 8
    assert not _evaluated(db, entry.id, doubled)
    assert not _evaluated(db, entry.id, quadrupled)


def test_compute_for_entry_without_readers_of_the_change_reads_nothing(
    db, database_block, entry, two_chains, count_queries,
):
    from app.blocks.computed import compute_for_entry

    schemas = repo.list_schemas(db, database_block.id)
    database_id, entry_id = database_block.id, entry.id

    with count_queries() as counter:
        assert compute_for_entry(
            db, database_id, entry_id, {uuid.uuid4()}, schemas=schemas,
        ) == set()

    assert counter.count == 0


def test_compute_for_entry_always_evaluates_time_dependent_formulas(
    db, database_block, entry, two_chains,
):
    from app.blocks.computed import compute_for_entry

    _, b, doubled, _, _ = two_chains
    clock = repo.create_schema(
        db, database_id=database_block.id, name="Clock", type="formula",
        position=9.0, config={"expression": "year(now())"},
    )
    db.commit()
    _plant(db, entry.id, clock, doubled)

    compute_for_entry(db, database_block.id, entry.id, {b.id})
    db.commit()

    assert _evaluated(db, entry.id, clock)
    assert not _evaluated(db, entry.id, doubled)


def test_compute_for_entry_reports_a_cycle_however_it_is_called(db, database_block, entry):
    from app.blocks.computed import compute_for_entry

    first = repo.create_schema(
        db, database_id=database_block.id, name="First", type="formula",
        position=1.0, config={"expression": "prop('Second') + 1"},
    )
    second = repo.create_schema(
        db, database_id=database_block.id, name="Second", type="formula",
        position=2.0, config={"expression": "prop('First') + 1"},
    )
    db.commit()

    compute_for_entry(db, database_block.id, entry.id, {uuid.uuid4()})
    db.commit()

    for schema in (first, second):
        assert repo.get_value(db, entry.id, schema.id).value["error"]


def test_compute_all_still_evaluates_everything(db, database_block, entry, two_chains):
    _, _, doubled, quadrupled, other = two_chains
    _plant(db, entry.id, doubled, quadrupled, other)

    assert compute_all_for_entry(db, database_block.id, entry.id) == {
        doubled.id, quadrupled.id, other.id,
    }


# ─── Readers across databases ─────────────────────────────────────────────────


def test_cross_readers_name_each_reader_and_what_it_reads(db):
    from app.blocks.computed import build_cross_readers

    source_id = uuid.uuid4()
    column = uuid.uuid4()
    rel = _schema("Link", "relation", {"target_database_id": str(source_id)})
    rollup = _schema("Sum", "rollup", {
        "relation_schema_id": str(rel.id), "rollup_schema_id": str(column),
        "function": "sum",
    })
    unfinished = _schema("Count", "rollup", {"relation_schema_id": str(rel.id)})
    titles = _schema("Titles", "formula", {"expression": "prop('Link')"})

    readers = build_cross_readers(db, [rel, rollup, unfinished, titles], source_id)

    assert sorted(readers[rel.id], key=lambda r: str(r[1])) == sorted(
        [(column, rollup.id), (None, unfinished.id), (READS_TITLE, titles.id)],
        key=lambda r: str(r[1]),
    )


@pytest.fixture
def two_rollups_one_relation(db, workspace):
    """
    ``readers`` rolls up two columns of ``places`` through the same relation:
    ``Regions`` reads ``Region``, ``Sizes`` reads ``Size``.
    """
    places = repo.create_block(db, type="database", position=1.0, parent_id=workspace.id)
    place = repo.create_block(db, type="page", position=1.0, parent_id=places.id)
    region = repo.create_schema(db, database_id=places.id, name="Region", type="text", position=1.0)
    size = repo.create_schema(db, database_id=places.id, name="Size", type="number", position=2.0)
    readers = repo.create_block(db, type="database", position=2.0, parent_id=workspace.id)
    rel = repo.create_schema(
        db, database_id=readers.id, name="Place", type="relation",
        position=1.0, config={"target_database_id": str(places.id)},
    )
    regions = repo.create_schema(
        db, database_id=readers.id, name="Regions", type="rollup", position=2.0,
        config={"relation_schema_id": str(rel.id), "rollup_schema_id": str(region.id),
                "function": "show_original"},
    )
    sizes = repo.create_schema(
        db, database_id=readers.id, name="Sizes", type="rollup", position=3.0,
        config={"relation_schema_id": str(rel.id), "rollup_schema_id": str(size.id),
                "function": "sum"},
    )
    reader = repo.create_block(db, type="page", position=1.0, parent_id=readers.id)
    repo.upsert_value(db, page_id=reader.id, schema_id=rel.id,
                      value={"related_ids": [str(place.id)]})
    repo.upsert_value(db, page_id=place.id, schema_id=region.id, value={"text": "Value A"})
    db.commit()
    return places, place, region, readers, reader, regions, sizes


def test_a_reader_evaluates_only_the_rollup_reading_the_change(db, two_rollups_one_relation):
    places, place, region, readers, reader, regions, sizes = two_rollups_one_relation
    _plant(db, reader.id, regions, sizes)

    assert recompute_after_value_change(db, places.id, place.id, {region.id}) == [readers.id]
    db.commit()

    assert repo.get_value(db, reader.id, regions.id).value["result"] == ["Value A"]
    assert not _evaluated(db, reader.id, sizes)


def test_a_title_change_evaluates_every_reader_without_a_column_list(
    db, two_rollups_one_relation,
):
    places, place, _, readers, reader, regions, sizes = two_rollups_one_relation
    _plant(db, reader.id, regions, sizes)

    assert compute_cross_db_dependents(db, places.id, place.id) == [readers.id]
    db.commit()

    assert _evaluated(db, reader.id, regions)
    assert _evaluated(db, reader.id, sizes)


def test_an_entry_linking_to_itself_follows_its_own_change(db, database_block, entry):
    """
    A rollup over a link from an entry to itself reads the changed column
    through the relation; it depends on the relation, not on the column, so
    the entry's own pass alone would leave it as it was.
    """
    rel = repo.create_schema(
        db, database_id=database_block.id, name="Rel", type="relation",
        position=1.0, config={"target_database_id": str(database_block.id)},
    )
    amount = repo.create_schema(
        db, database_id=database_block.id, name="Amount", type="number", position=2.0,
    )
    total = repo.create_schema(
        db, database_id=database_block.id, name="Total", type="rollup", position=3.0,
        config={"relation_schema_id": str(rel.id), "rollup_schema_id": str(amount.id),
                "function": "sum"},
    )
    repo.upsert_value(db, page_id=entry.id, schema_id=rel.id,
                      value={"related_ids": [str(entry.id)]})
    repo.upsert_value(db, page_id=entry.id, schema_id=amount.id, value={"number": 3})
    db.commit()
    compute_all_for_entry(db, database_block.id, entry.id)
    db.commit()
    repo.upsert_value(db, page_id=entry.id, schema_id=amount.id, value={"number": 5})
    db.commit()

    assert recompute_after_value_change(db, database_block.id, entry.id, {amount.id}) == []
    db.commit()

    assert repo.get_value(db, entry.id, total.id).value["result"] == pytest.approx(5.0)
    assert compute_same_db_rollup_dependents(db, database_block.id, entry.id) is False


def test_schema_index_loads_all_databases_at_once(db, two_rollups_one_relation, count_queries):
    from app.blocks.computed import SchemaIndex

    places, _, region, readers, _, _, _ = two_rollups_one_relation
    places_id, readers_id, region_id = places.id, readers.id, region.id

    with count_queries() as counter:
        index = SchemaIndex.load(db)
        elsewhere = index.readers_elsewhere(places_id)
        place_schemas = index.schemas(places_id)

    assert counter.count == 1
    assert [database_id for database_id, _ in elsewhere] == [readers_id]
    assert region_id in {s.id for s in place_schemas}
    assert index.readers_elsewhere(readers_id) == []


def test_schema_index_looks_up_a_database_it_does_not_hold(db, workspace):
    from app.blocks.computed import SchemaIndex

    trashed = repo.create_block(
        db, type="database", position=1.0, parent_id=workspace.id, state="trash",
    )
    schema = repo.create_schema(db, database_id=trashed.id, name="A", type="text", position=1.0)
    db.commit()

    index = SchemaIndex.load(db)

    assert [s.id for s in index.schemas(trashed.id)] == [schema.id]


def test_recompute_cost_does_not_grow_with_unrelated_databases(
    db, two_rollups_one_relation, workspace, count_queries,
):
    """
    The schemas of every database used to be loaded one database at a time,
    for the entry and again for every mirror entry of the same request.
    """
    places, place, region, _, _, _, _ = two_rollups_one_relation
    places_id, place_id, region_id = places.id, place.id, region.id
    workspace_id = workspace.id
    recompute_after_value_change(db, places_id, place_id, {region_id})
    db.commit()
    db.expunge_all()
    with count_queries() as few:
        recompute_after_value_change(db, places_id, place_id, {region_id})
    db.commit()

    for i in range(6):
        unrelated = repo.create_block(
            db, type="database", position=10.0 + i, parent_id=workspace_id,
        )
        repo.create_schema(db, database_id=unrelated.id, name="Note", type="text", position=1.0)
    db.commit()
    db.expunge_all()
    with count_queries() as many:
        recompute_after_value_change(db, places_id, place_id, {region_id})
    db.commit()

    assert many.count == few.count
