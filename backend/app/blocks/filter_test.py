"""
Comprehensive filter tests for query_entries().

Covers every FilterOperator against every schema type that supports it,
including:
  - name column (title)
  - text, number, checkbox
  - select single, select multi
  - date (all presets + point comparisons + all dateMode variants)
  - created_time / last_edited_time (datetime key, ISO-8601 with time component)
  - created_by / last_edited_by (username key)
  - email, phone, url (value key)
  - relation (is_empty / is_not_empty / contains / not_contains on related_ids array)
  - file (is_empty / is_not_empty on files array)
  - id schema (numeric comparisons on id_value key)
  - combined AND filters
  - sort edge cases

Why this file exists
--------------------
Filter logic lives in repository._build_filter_clause().  Manual UI testing
is slow and misses edge cases.  These tests catch regressions automatically
and document the exact semantics guaranteed — for example:

  * A checkbox entry that was never toggled (no PropertyValue row) is
    treated as "false", so `eq 'false'` must include it.
  * `is_empty` on a text column returns entries with no row OR empty string.
  * Date preset operators are computed relative to today's date at query time.
  * created_time / last_edited_time store ISO-8601 datetimes; filters truncate
    the time component to the date prefix for comparison.

Running
-------
    pytest backend/app/blocks/filter_test.py -v
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

import app.session.session as s
from app.blocks import repository as repo
from app.blocks.models import WORKSPACE_ROOT_ID, Block


# ─── Fixtures ─────────────────────────────────────────────────────────────────


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


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _entry(db, database_block, title: str | None = None, pos: float = 1.0) -> Block:
    e = repo.create_block(
        db, type="page", position=pos, parent_id=database_block.id,
        content={"title": title} if title else None,
    )
    db.commit()
    return e


def _schema(db, database_block, name: str, type_: str, config: dict | None = None):
    s_ = repo.create_schema(
        db, database_id=database_block.id, name=name, type=type_,
        position=1.0, config=config,
    )
    db.commit()
    return s_


def _val(db, entry, schema, value: dict | None):
    repo.upsert_value(db, page_id=entry.id, schema_id=schema.id, value=value)
    db.commit()


def _f(schema_id, operator, value="", *, schema_type=None, schema_config=None,
       date_mode=None, date_offset=None, value2=None) -> repo.FilterDescriptor:
    return repo.FilterDescriptor(
        schema_id=schema_id,
        schema_type=schema_type,
        schema_config=schema_config,
        operator=operator,
        value=value,
        date_mode=date_mode,
        date_offset=date_offset,
        value2=value2,
    )


def _name_f(operator, value="") -> repo.FilterDescriptor:
    return _f("__name__", operator, value)


def _ids(entries) -> set[uuid.UUID]:
    return {e.id for e in entries}


def _and_group(*filters) -> repo.FilterGroupDescriptor:
    """Convenience: wrap filters in a single AND group."""
    return repo.FilterGroupDescriptor(conjunction='and', filters=list(filters))


def _or_group(*filters) -> repo.FilterGroupDescriptor:
    """Convenience: wrap filters in a single OR group."""
    return repo.FilterGroupDescriptor(conjunction='or', filters=list(filters))


def _run(db, database_block, filters, sorts=None) -> tuple[list[Block], int]:
    """Run a query with a single implicit AND group containing the given filters."""
    groups = [_and_group(*filters)] if filters else []
    return repo.query_entries(db, database_block.id, groups, sorts or [])


def _run_groups(db, database_block, groups, sorts=None) -> tuple[list[Block], int]:
    """Run a query with explicit filter groups."""
    return repo.query_entries(db, database_block.id, groups, sorts or [])


# ─── Name column filters ───────────────────────────────────────────────────────


class TestNameColumnFilters:

    def test_contains_matches_substring(self, db, database_block):
        e1 = _entry(db, database_block, "Napoleon Bonaparte", pos=1.0)
        e2 = _entry(db, database_block, "Wellington",        pos=2.0)
        entries, total = _run(db, database_block, [_name_f("contains", "leon")])
        assert total == 1
        assert _ids(entries) == {e1.id}

    def test_contains_is_case_insensitive(self, db, database_block):
        e1 = _entry(db, database_block, "Napoleon", pos=1.0)
        _entry(db, database_block, "Wellington", pos=2.0)
        entries, _ = _run(db, database_block, [_name_f("contains", "NAPOLEON")])
        assert _ids(entries) == {e1.id}

    def test_not_contains(self, db, database_block):
        e1 = _entry(db, database_block, "Napoleon", pos=1.0)
        e2 = _entry(db, database_block, "Wellington", pos=2.0)
        entries, _ = _run(db, database_block, [_name_f("not_contains", "leon")])
        assert _ids(entries) == {e2.id}

    def test_starts_with(self, db, database_block):
        e1 = _entry(db, database_block, "Napoleon", pos=1.0)
        e2 = _entry(db, database_block, "Wellington", pos=2.0)
        entries, _ = _run(db, database_block, [_name_f("starts_with", "nap")])
        assert _ids(entries) == {e1.id}

    def test_ends_with(self, db, database_block):
        e1 = _entry(db, database_block, "Napoleon", pos=1.0)
        e2 = _entry(db, database_block, "Wellington", pos=2.0)
        entries, _ = _run(db, database_block, [_name_f("ends_with", "ton")])
        assert _ids(entries) == {e2.id}

    def test_eq(self, db, database_block):
        e1 = _entry(db, database_block, "Napoleon", pos=1.0)
        _entry(db, database_block, "Wellington", pos=2.0)
        entries, _ = _run(db, database_block, [_name_f("eq", "napoleon")])
        assert _ids(entries) == {e1.id}

    def test_neq(self, db, database_block):
        e1 = _entry(db, database_block, "Napoleon", pos=1.0)
        e2 = _entry(db, database_block, "Wellington", pos=2.0)
        entries, _ = _run(db, database_block, [_name_f("neq", "napoleon")])
        assert _ids(entries) == {e2.id}

    def test_is_empty_no_title(self, db, database_block):
        e1 = _entry(db, database_block, title=None, pos=1.0)
        e2 = _entry(db, database_block, "Napoleon", pos=2.0)
        entries, _ = _run(db, database_block, [_name_f("is_empty")])
        assert e1.id in _ids(entries)
        assert e2.id not in _ids(entries)

    def test_is_not_empty(self, db, database_block):
        _entry(db, database_block, title=None, pos=1.0)
        e2 = _entry(db, database_block, "Napoleon", pos=2.0)
        entries, _ = _run(db, database_block, [_name_f("is_not_empty")])
        assert _ids(entries) == {e2.id}


# ─── Text schema filters ───────────────────────────────────────────────────────


class TestTextSchemaFilters:

    @pytest.fixture
    def setup(self, db, database_block):
        schema = _schema(db, database_block, "Unit", "text")
        e1 = _entry(db, database_block, pos=1.0)
        e2 = _entry(db, database_block, pos=2.0)
        e3 = _entry(db, database_block, pos=3.0)
        _val(db, e1, schema, {"text": "cavalry"})
        _val(db, e2, schema, {"text": "infantry"})
        return schema, e1, e2, e3

    def _tf(self, schema, op, val=""):
        return _f(str(schema.id), op, val, schema_type="text")

    def test_contains(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, total = _run(db, database_block, [self._tf(schema, "contains", "cavalry")])
        assert total == 1 and _ids(entries) == {e1.id}

    def test_not_contains(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, _ = _run(db, database_block, [self._tf(schema, "not_contains", "cavalry")])
        assert e2.id in _ids(entries) and e1.id not in _ids(entries)

    def test_starts_with(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, _ = _run(db, database_block, [self._tf(schema, "starts_with", "cav")])
        assert _ids(entries) == {e1.id}

    def test_ends_with(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, _ = _run(db, database_block, [self._tf(schema, "ends_with", "try")])
        assert _ids(entries) == {e2.id}

    def test_eq(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, _ = _run(db, database_block, [self._tf(schema, "eq", "cavalry")])
        assert _ids(entries) == {e1.id}

    def test_neq(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, _ = _run(db, database_block, [self._tf(schema, "neq", "cavalry")])
        assert e1.id not in _ids(entries) and e2.id in _ids(entries)

    def test_is_empty_no_row(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, _ = _run(db, database_block, [self._tf(schema, "is_empty")])
        assert e3.id in _ids(entries) and e1.id not in _ids(entries)

    def test_is_not_empty(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, _ = _run(db, database_block, [self._tf(schema, "is_not_empty")])
        assert e3.id not in _ids(entries)
        assert e1.id in _ids(entries) and e2.id in _ids(entries)


# ─── Number schema filters ────────────────────────────────────────────────────


class TestNumberSchemaFilters:

    @pytest.fixture
    def setup(self, db, database_block):
        schema = _schema(db, database_block, "Strength", "number")
        e1 = _entry(db, database_block, pos=1.0)
        e2 = _entry(db, database_block, pos=2.0)
        e3 = _entry(db, database_block, pos=3.0)
        e4 = _entry(db, database_block, pos=4.0)
        _val(db, e1, schema, {"number": 1})
        _val(db, e2, schema, {"number": 5})
        _val(db, e3, schema, {"number": 10})
        return schema, e1, e2, e3, e4

    def _nf(self, schema, op, val=""):
        return _f(str(schema.id), op, val, schema_type="number")

    def test_eq(self, db, database_block, setup):
        schema, e1, e2, e3, e4 = setup
        entries, _ = _run(db, database_block, [self._nf(schema, "eq", "5")])
        assert _ids(entries) == {e2.id}

    def test_neq(self, db, database_block, setup):
        schema, e1, e2, e3, e4 = setup
        entries, _ = _run(db, database_block, [self._nf(schema, "neq", "5")])
        assert e2.id not in _ids(entries)
        assert e1.id in _ids(entries) and e3.id in _ids(entries)

    def test_gt(self, db, database_block, setup):
        schema, e1, e2, e3, e4 = setup
        entries, total = _run(db, database_block, [self._nf(schema, "gt", "4")])
        assert total == 2 and _ids(entries) == {e2.id, e3.id}

    def test_gte(self, db, database_block, setup):
        schema, e1, e2, e3, e4 = setup
        entries, total = _run(db, database_block, [self._nf(schema, "gte", "5")])
        assert total == 2 and _ids(entries) == {e2.id, e3.id}

    def test_lt(self, db, database_block, setup):
        schema, e1, e2, e3, e4 = setup
        entries, _ = _run(db, database_block, [self._nf(schema, "lt", "5")])
        assert _ids(entries) == {e1.id}

    def test_lte(self, db, database_block, setup):
        schema, e1, e2, e3, e4 = setup
        entries, _ = _run(db, database_block, [self._nf(schema, "lte", "5")])
        assert _ids(entries) == {e1.id, e2.id}

    def test_is_empty(self, db, database_block, setup):
        schema, e1, e2, e3, e4 = setup
        entries, total = _run(db, database_block, [self._nf(schema, "is_empty")])
        assert total == 1 and _ids(entries) == {e4.id}

    def test_is_not_empty(self, db, database_block, setup):
        schema, e1, e2, e3, e4 = setup
        entries, total = _run(db, database_block, [self._nf(schema, "is_not_empty")])
        assert total == 3 and e4.id not in _ids(entries)


# ─── ID schema filters ────────────────────────────────────────────────────────


class TestIdSchemaFilters:

    @pytest.fixture
    def setup(self, db, database_block):
        schema = _schema(db, database_block, "ID", "id", config={"prefix": "", "next_id": 1})
        e1 = _entry(db, database_block, pos=1.0)
        e2 = _entry(db, database_block, pos=2.0)
        e3 = _entry(db, database_block, pos=3.0)
        _val(db, e1, schema, {"id_value": 1})
        _val(db, e2, schema, {"id_value": 2})
        _val(db, e3, schema, {"id_value": 3})
        return schema, e1, e2, e3

    def _idf(self, schema, op, val=""):
        return _f(str(schema.id), op, val, schema_type="id")

    def test_eq(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, _ = _run(db, database_block, [self._idf(schema, "eq", "2")])
        assert _ids(entries) == {e2.id}

    def test_gt(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, total = _run(db, database_block, [self._idf(schema, "gt", "1")])
        assert total == 2 and _ids(entries) == {e2.id, e3.id}

    def test_lte(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, _ = _run(db, database_block, [self._idf(schema, "lte", "2")])
        assert _ids(entries) == {e1.id, e2.id}

    def test_is_empty(self, db, database_block):
        schema = _schema(db, database_block, "ID", "id")
        e1 = _entry(db, database_block, pos=1.0)
        e2 = _entry(db, database_block, pos=2.0)
        _val(db, e1, schema, {"id_value": 1})
        entries, total = _run(db, database_block,
            [_f(str(schema.id), "is_empty", schema_type="id")])
        assert total == 1 and _ids(entries) == {e2.id}


# ─── Checkbox schema filters ──────────────────────────────────────────────────


class TestCheckboxSchemaFilters:
    """
    The most subtle schema type. "False" has two DB representations:
      1. No PropertyValue row (never toggled)
      2. A row with value = {"checked": false}
    Both must behave identically for all operators.
    """

    @pytest.fixture
    def setup(self, db, database_block):
        schema = _schema(db, database_block, "Done", "checkbox")
        e_true   = _entry(db, database_block, "True entry",           pos=1.0)
        e_false  = _entry(db, database_block, "Explicit false entry", pos=2.0)
        e_no_row = _entry(db, database_block, "No-row false entry",   pos=3.0)
        _val(db, e_true,  schema, {"checked": True})
        _val(db, e_false, schema, {"checked": False})
        # e_no_row: no PropertyValue row written — visual default is false
        return schema, e_true, e_false, e_no_row

    def _cf(self, schema, op, val=""):
        return _f(str(schema.id), op, val, schema_type="checkbox")

    def test_eq_true_returns_only_true_entry(self, db, database_block, setup):
        schema, e_true, e_false, e_no_row = setup
        entries, total = _run(db, database_block, [self._cf(schema, "eq", "true")])
        assert total == 1
        assert _ids(entries) == {e_true.id}

    def test_eq_false_returns_both_false_representations(self, db, database_block, setup):
        """eq 'false' must match both explicit-false AND the no-row entry."""
        schema, e_true, e_false, e_no_row = setup
        entries, total = _run(db, database_block, [self._cf(schema, "eq", "false")])
        assert total == 2
        assert _ids(entries) == {e_false.id, e_no_row.id}

    def test_neq_true_returns_both_false_representations(self, db, database_block, setup):
        schema, e_true, e_false, e_no_row = setup
        entries, total = _run(db, database_block, [self._cf(schema, "neq", "true")])
        assert total == 2
        assert _ids(entries) == {e_false.id, e_no_row.id}

    def test_neq_false_returns_only_true_entry(self, db, database_block, setup):
        schema, e_true, e_false, e_no_row = setup
        entries, total = _run(db, database_block, [self._cf(schema, "neq", "false")])
        assert total == 1
        assert _ids(entries) == {e_true.id}

    def test_eq_empty_value_treated_as_false(self, db, database_block, setup):
        """
        When the filter value is '' (the UI default before the user interacts
        with the dropdown), eq '' must behave identically to eq 'false'.
        This guards against the bug where the UI initialised value='' and the
        backend fell into the wrong branch of the checkbox logic.
        """
        schema, e_true, e_false, e_no_row = setup
        entries, total = _run(db, database_block, [self._cf(schema, "eq", "")])
        assert total == 2
        assert _ids(entries) == {e_false.id, e_no_row.id}

    def test_neq_empty_value_treated_as_false(self, db, database_block, setup):
        """neq '' should also return only the true entry, same as neq 'false'."""
        schema, e_true, e_false, e_no_row = setup
        entries, total = _run(db, database_block, [self._cf(schema, "neq", "")])
        assert total == 1
        assert _ids(entries) == {e_true.id}

    def test_is_empty_returns_no_row_entry_only(self, db, database_block, setup):
        """is_empty checks for a missing/null row — explicit false IS a row."""
        schema, e_true, e_false, e_no_row = setup
        entries, _ = _run(db, database_block, [self._cf(schema, "is_empty")])
        assert e_no_row.id in _ids(entries)
        assert e_true.id not in _ids(entries)
        assert e_false.id not in _ids(entries)

    def test_is_not_empty_returns_entries_with_rows(self, db, database_block, setup):
        schema, e_true, e_false, e_no_row = setup
        entries, _ = _run(db, database_block, [self._cf(schema, "is_not_empty")])
        assert e_true.id in _ids(entries)
        assert e_false.id in _ids(entries)
        assert e_no_row.id not in _ids(entries)


# ─── Select (single) schema filters ───────────────────────────────────────────


class TestSingleSelectFilters:

    @pytest.fixture
    def setup(self, db, database_block):
        schema = _schema(db, database_block, "Status", "select",
                         config={"options": ["Todo", "Done", "Blocked"], "mode": "single"})
        e1 = _entry(db, database_block, pos=1.0)
        e2 = _entry(db, database_block, pos=2.0)
        e3 = _entry(db, database_block, pos=3.0)
        _val(db, e1, schema, {"option": "Todo"})
        _val(db, e2, schema, {"option": "Done"})
        return schema, e1, e2, e3

    def _sf(self, schema, op, val=""):
        return _f(str(schema.id), op, val, schema_type="select",
                  schema_config={"mode": "single"})

    def test_eq(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, _ = _run(db, database_block, [self._sf(schema, "eq", "todo")])
        assert _ids(entries) == {e1.id}

    def test_neq(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, _ = _run(db, database_block, [self._sf(schema, "neq", "todo")])
        assert e1.id not in _ids(entries) and e2.id in _ids(entries)

    def test_is_empty(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, total = _run(db, database_block, [self._sf(schema, "is_empty")])
        assert total == 1 and _ids(entries) == {e3.id}

    def test_is_not_empty(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, total = _run(db, database_block, [self._sf(schema, "is_not_empty")])
        assert total == 2 and e3.id not in _ids(entries)


# ─── Select (multi) schema filters ────────────────────────────────────────────


class TestMultiSelectFilters:

    @pytest.fixture
    def setup(self, db, database_block):
        schema = _schema(db, database_block, "Tags", "select",
                         config={"options": ["Alpha", "Beta", "Gamma"], "mode": "multiple"})
        e1 = _entry(db, database_block, pos=1.0)
        e2 = _entry(db, database_block, pos=2.0)
        e3 = _entry(db, database_block, pos=3.0)
        _val(db, e1, schema, {"options": ["Alpha", "Beta"]})
        _val(db, e2, schema, {"options": ["Beta", "Gamma"]})
        return schema, e1, e2, e3

    def _mf(self, schema, op, val=""):
        return _f(str(schema.id), op, val, schema_type="select",
                  schema_config={"mode": "multiple"})

    def test_contains_matches_entries_having_option(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, total = _run(db, database_block, [self._mf(schema, "contains", "Beta")])
        assert total == 2 and _ids(entries) == {e1.id, e2.id}

    def test_contains_is_case_insensitive(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, _ = _run(db, database_block, [self._mf(schema, "contains", "alpha")])
        assert _ids(entries) == {e1.id}

    def test_not_contains(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, _ = _run(db, database_block, [self._mf(schema, "not_contains", "Alpha")])
        assert e1.id not in _ids(entries)

    def test_is_empty(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, total = _run(db, database_block, [self._mf(schema, "is_empty")])
        assert total == 1 and _ids(entries) == {e3.id}

    def test_is_not_empty(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, total = _run(db, database_block, [self._mf(schema, "is_not_empty")])
        assert total == 2 and e3.id not in _ids(entries)

    def test_contains_empty_value_is_noop(self, db, database_block, setup):
        """
        An empty filter value (UI default before the user picks an option)
        must not crash or collapse the view to zero results.
        Mirrors the identical guard in the relation contains branch.
        All three entries must be returned because no meaningful constraint
        is expressed.
        """
        schema, e1, e2, e3 = setup
        entries, total = _run(db, database_block, [self._mf(schema, "contains", "")])
        assert total == 3

    def test_not_contains_empty_value_is_noop(self, db, database_block, setup):
        """not_contains '' must be a no-op for the same reason as contains ''."""
        schema, e1, e2, e3 = setup
        entries, total = _run(db, database_block, [self._mf(schema, "not_contains", "")])
        assert total == 3


# ─── Date schema filters ───────────────────────────────────────────────────────


class TestDateSchemaFilters:

    @pytest.fixture
    def setup(self, db, database_block):
        schema = _schema(db, database_block, "Due", "date")
        today = date.today()
        e_past   = _entry(db, database_block, pos=1.0)
        e_today  = _entry(db, database_block, pos=2.0)
        e_future = _entry(db, database_block, pos=3.0)
        e_empty  = _entry(db, database_block, pos=4.0)
        _val(db, e_past,   schema, {"start": (today - timedelta(days=10)).isoformat()})
        _val(db, e_today,  schema, {"start": today.isoformat()})
        _val(db, e_future, schema, {"start": (today + timedelta(days=10)).isoformat()})
        return schema, e_past, e_today, e_future, e_empty, today

    def _df(self, schema, op, val="", *, date_mode=None, date_offset=None):
        return _f(str(schema.id), op, val, schema_type="date",
                  date_mode=date_mode, date_offset=date_offset)

    def test_eq_exact(self, db, database_block, setup):
        schema, e_past, e_today, e_future, e_empty, today = setup
        entries, _ = _run(db, database_block,
            [self._df(schema, "eq", today.isoformat(), date_mode="exact")])
        assert _ids(entries) == {e_today.id}

    def test_eq_today_mode(self, db, database_block, setup):
        schema, e_past, e_today, e_future, e_empty, today = setup
        entries, _ = _run(db, database_block, [self._df(schema, "eq", date_mode="today")])
        assert _ids(entries) == {e_today.id}

    def test_eq_relative_zero(self, db, database_block, setup):
        schema, e_past, e_today, e_future, e_empty, today = setup
        entries, _ = _run(db, database_block,
            [self._df(schema, "eq", date_mode="relative", date_offset=0)])
        assert _ids(entries) == {e_today.id}

    def test_eq_relative_future(self, db, database_block, setup):
        schema, e_past, e_today, e_future, e_empty, today = setup
        entries, _ = _run(db, database_block,
            [self._df(schema, "eq", date_mode="relative", date_offset=10)])
        assert _ids(entries) == {e_future.id}

    def test_gt_today(self, db, database_block, setup):
        schema, e_past, e_today, e_future, e_empty, today = setup
        entries, _ = _run(db, database_block, [self._df(schema, "gt", date_mode="today")])
        assert _ids(entries) == {e_future.id}

    def test_gte_today(self, db, database_block, setup):
        schema, e_past, e_today, e_future, e_empty, today = setup
        entries, _ = _run(db, database_block, [self._df(schema, "gte", date_mode="today")])
        assert _ids(entries) == {e_today.id, e_future.id}

    def test_lt_today(self, db, database_block, setup):
        schema, e_past, e_today, e_future, e_empty, today = setup
        entries, _ = _run(db, database_block, [self._df(schema, "lt", date_mode="today")])
        assert _ids(entries) == {e_past.id}

    def test_lte_today(self, db, database_block, setup):
        schema, e_past, e_today, e_future, e_empty, today = setup
        entries, _ = _run(db, database_block, [self._df(schema, "lte", date_mode="today")])
        assert _ids(entries) == {e_past.id, e_today.id}

    def test_is_empty(self, db, database_block, setup):
        schema, e_past, e_today, e_future, e_empty, today = setup
        entries, total = _run(db, database_block, [self._df(schema, "is_empty")])
        assert total == 1 and _ids(entries) == {e_empty.id}

    def test_is_not_empty(self, db, database_block, setup):
        schema, e_past, e_today, e_future, e_empty, today = setup
        entries, total = _run(db, database_block, [self._df(schema, "is_not_empty")])
        assert total == 3 and e_empty.id not in _ids(entries)

    def test_past_week_includes_today(self, db, database_block, setup):
        schema, e_past, e_today, e_future, e_empty, today = setup
        entries, _ = _run(db, database_block, [self._df(schema, "past_week")])
        assert e_today.id in _ids(entries)
        assert e_future.id not in _ids(entries)

    def test_past_month_includes_10_days_ago(self, db, database_block, setup):
        schema, e_past, e_today, e_future, e_empty, today = setup
        entries, _ = _run(db, database_block, [self._df(schema, "past_month")])
        assert e_past.id in _ids(entries)
        assert e_future.id not in _ids(entries)

    def test_past_year_includes_past(self, db, database_block, setup):
        schema, e_past, e_today, e_future, e_empty, today = setup
        entries, _ = _run(db, database_block, [self._df(schema, "past_year")])
        assert e_past.id in _ids(entries)
        assert e_future.id not in _ids(entries)

    def test_next_week_excludes_past(self, db, database_block, setup):
        schema, e_past, e_today, e_future, e_empty, today = setup
        entries, _ = _run(db, database_block, [self._df(schema, "next_week")])
        assert e_past.id not in _ids(entries)

    def test_next_month_includes_10_days_future(self, db, database_block, setup):
        schema, e_past, e_today, e_future, e_empty, today = setup
        entries, _ = _run(db, database_block, [self._df(schema, "next_month")])
        assert e_future.id in _ids(entries)
        assert e_past.id not in _ids(entries)

    def test_next_year_includes_future(self, db, database_block, setup):
        schema, e_past, e_today, e_future, e_empty, today = setup
        entries, _ = _run(db, database_block, [self._df(schema, "next_year")])
        assert e_future.id in _ids(entries)
        assert e_past.id not in _ids(entries)

    def test_this_week_includes_today(self, db, database_block, setup):
        schema, e_past, e_today, e_future, e_empty, today = setup
        entries, _ = _run(db, database_block, [self._df(schema, "this_week")])
        assert e_today.id in _ids(entries)


# ─── created_time / last_edited_time filters ──────────────────────────────────


class TestDatetimeSchemaFilters:
    """
    created_time and last_edited_time store ISO-8601 datetime strings under the
    'datetime' key.  The filter truncates to the date prefix for comparison.
    """

    @pytest.fixture
    def setup(self, db, database_block):
        schema = _schema(db, database_block, "Created", "created_time")
        today = date.today()
        e_past   = _entry(db, database_block, pos=1.0)
        e_today  = _entry(db, database_block, pos=2.0)
        e_future = _entry(db, database_block, pos=3.0)
        e_empty  = _entry(db, database_block, pos=4.0)
        past_dt   = datetime(today.year, today.month, today.day, 9, 0, 0,
                             tzinfo=timezone.utc) - timedelta(days=5)
        today_dt  = datetime(today.year, today.month, today.day, 14, 30, 0,
                             tzinfo=timezone.utc)
        future_dt = datetime(today.year, today.month, today.day, 8, 0, 0,
                             tzinfo=timezone.utc) + timedelta(days=5)
        _val(db, e_past,   schema, {"datetime": past_dt.isoformat()})
        _val(db, e_today,  schema, {"datetime": today_dt.isoformat()})
        _val(db, e_future, schema, {"datetime": future_dt.isoformat()})
        return schema, e_past, e_today, e_future, e_empty, today

    def _dtf(self, schema, op, val="", *, date_mode=None, date_offset=None):
        return _f(str(schema.id), op, val, schema_type="created_time",
                  date_mode=date_mode, date_offset=date_offset)

    def test_eq_today_mode(self, db, database_block, setup):
        schema, e_past, e_today, e_future, e_empty, today = setup
        entries, _ = _run(db, database_block, [self._dtf(schema, "eq", date_mode="today")])
        assert _ids(entries) == {e_today.id}

    def test_lt_today(self, db, database_block, setup):
        schema, e_past, e_today, e_future, e_empty, today = setup
        entries, _ = _run(db, database_block, [self._dtf(schema, "lt", date_mode="today")])
        assert _ids(entries) == {e_past.id}

    def test_gt_today(self, db, database_block, setup):
        schema, e_past, e_today, e_future, e_empty, today = setup
        entries, _ = _run(db, database_block, [self._dtf(schema, "gt", date_mode="today")])
        assert _ids(entries) == {e_future.id}

    def test_is_empty(self, db, database_block, setup):
        schema, e_past, e_today, e_future, e_empty, today = setup
        entries, total = _run(db, database_block, [self._dtf(schema, "is_empty")])
        assert total == 1 and _ids(entries) == {e_empty.id}

    def test_past_week_preset(self, db, database_block, setup):
        schema, e_past, e_today, e_future, e_empty, today = setup
        entries, _ = _run(db, database_block, [self._dtf(schema, "past_week")])
        assert e_today.id in _ids(entries)
        assert e_future.id not in _ids(entries)

    def test_last_edited_time_same_logic(self, db, database_block):
        schema = _schema(db, database_block, "Edited", "last_edited_time")
        today = date.today()
        e1 = _entry(db, database_block, pos=1.0)
        e2 = _entry(db, database_block, pos=2.0)
        now = datetime(today.year, today.month, today.day, 12, 0, tzinfo=timezone.utc)
        _val(db, e1, schema, {"datetime": now.isoformat()})
        entries, _ = _run(db, database_block,
            [_f(str(schema.id), "eq", date_mode="today", schema_type="last_edited_time")])
        assert _ids(entries) == {e1.id}



# ─── date 'between' filter (#60) ──────────────────────────────────────────────


class TestDateBetweenFilter:
    """
    #60: 'between' matches entries whose date falls within [start, end] inclusive.

    Both bounds are exact ISO date strings stored in value / value2.
    An incomplete filter (either bound missing) must silently return no results
    rather than raising an exception.
    """

    def _bf(self, schema, start: str, end: str) -> repo.FilterDescriptor:
        return _f(str(schema.id), 'between', start, schema_type='date', value2=end)

    def test_between_includes_start_and_end_bounds(self, db, database_block):
        schema   = _schema(db, database_block, "Due", "date")
        e_before = _entry(db, database_block, pos=1.0)
        e_start  = _entry(db, database_block, pos=2.0)
        e_mid    = _entry(db, database_block, pos=3.0)
        e_end    = _entry(db, database_block, pos=4.0)
        e_after  = _entry(db, database_block, pos=5.0)
        _val(db, e_before, schema, {"start": "2025-01-01", "end": "2025-01-01"})
        _val(db, e_start,  schema, {"start": "2025-03-01", "end": "2025-03-01"})
        _val(db, e_mid,    schema, {"start": "2025-06-15", "end": "2025-06-15"})
        _val(db, e_end,    schema, {"start": "2025-09-30", "end": "2025-09-30"})
        _val(db, e_after,  schema, {"start": "2025-12-31", "end": "2025-12-31"})
        entries, _ = _run(db, database_block, [self._bf(schema, "2025-03-01", "2025-09-30")])
        ids = _ids(entries)
        assert e_start.id in ids,  "start bound must be included"
        assert e_mid.id in ids,    "date within range must be included"
        assert e_end.id in ids,    "end bound must be included"
        assert e_before.id not in ids
        assert e_after.id not in ids

    def test_between_excludes_entries_without_value(self, db, database_block):
        schema  = _schema(db, database_block, "Due", "date")
        e_empty = _entry(db, database_block, pos=1.0)
        e_in    = _entry(db, database_block, pos=2.0)
        _val(db, e_in, schema, {"start": "2025-05-01", "end": "2025-05-01"})
        entries, _ = _run(db, database_block, [self._bf(schema, "2025-01-01", "2025-12-31")])
        ids = _ids(entries)
        assert e_in.id in ids
        assert e_empty.id not in ids, "entry with no PropertyValue row must not match"

    def test_between_single_day_range(self, db, database_block):
        """Start == end is a valid single-day range."""
        schema = _schema(db, database_block, "Due", "date")
        e_match = _entry(db, database_block, pos=1.0)
        e_other = _entry(db, database_block, pos=2.0)
        _val(db, e_match, schema, {"start": "2025-07-04", "end": "2025-07-04"})
        _val(db, e_other, schema, {"start": "2025-07-05", "end": "2025-07-05"})
        entries, _ = _run(db, database_block, [self._bf(schema, "2025-07-04", "2025-07-04")])
        assert _ids(entries) == {e_match.id}

    def test_between_missing_end_date_returns_nothing(self, db, database_block):
        """Incomplete filter (value2 absent) must not crash and must return no results."""
        schema = _schema(db, database_block, "Due", "date")
        e = _entry(db, database_block, pos=1.0)
        _val(db, e, schema, {"start": "2025-05-01", "end": "2025-05-01"})
        f = _f(str(schema.id), 'between', '2025-01-01', schema_type='date')  # value2 omitted
        entries, _ = _run(db, database_block, [f])
        assert e.id not in _ids(entries)

    def test_between_missing_start_date_returns_nothing(self, db, database_block):
        """Incomplete filter (value absent) must not crash and must return no results."""
        schema = _schema(db, database_block, "Due", "date")
        e = _entry(db, database_block, pos=1.0)
        _val(db, e, schema, {"start": "2025-05-01", "end": "2025-05-01"})
        f = _f(str(schema.id), 'between', '', schema_type='date', value2='2025-12-31')
        entries, _ = _run(db, database_block, [f])
        assert e.id not in _ids(entries)

    def test_between_created_time_schema(self, db, database_block):
        """'between' works for created_time (datetime key, truncated to date prefix)."""
        from datetime import datetime, timezone
        schema   = _schema(db, database_block, "Created", "created_time")
        e_before = _entry(db, database_block, pos=1.0)
        e_in     = _entry(db, database_block, pos=2.0)
        e_after  = _entry(db, database_block, pos=3.0)
        _val(db, e_before, schema,
             {"datetime": datetime(2025, 2, 28, 12, 0, tzinfo=timezone.utc).isoformat()})
        _val(db, e_in,     schema,
             {"datetime": datetime(2025, 6, 15, 9, 0, tzinfo=timezone.utc).isoformat()})
        _val(db, e_after,  schema,
             {"datetime": datetime(2025, 11, 1, 8, 0, tzinfo=timezone.utc).isoformat()})
        f = _f(str(schema.id), 'between', '2025-03-01', schema_type='created_time',
               value2='2025-09-30')
        entries, _ = _run(db, database_block, [f])
        ids = _ids(entries)
        assert e_in.id in ids
        assert e_before.id not in ids
        assert e_after.id not in ids


# ─── created_by / last_edited_by filters ──────────────────────────────────────


class TestUserSchemaFilters:

    @pytest.fixture
    def setup(self, db, database_block):
        schema = _schema(db, database_block, "Created by", "created_by")
        e1 = _entry(db, database_block, pos=1.0)
        e2 = _entry(db, database_block, pos=2.0)
        e3 = _entry(db, database_block, pos=3.0)
        _val(db, e1, schema, {"username": "alice"})
        _val(db, e2, schema, {"username": "bob"})
        return schema, e1, e2, e3

    def _uf(self, schema, op, val=""):
        return _f(str(schema.id), op, val, schema_type="created_by")

    def test_contains(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, _ = _run(db, database_block, [self._uf(schema, "contains", "ali")])
        assert _ids(entries) == {e1.id}

    def test_not_contains(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, _ = _run(db, database_block, [self._uf(schema, "not_contains", "ali")])
        assert e1.id not in _ids(entries) and e2.id in _ids(entries)

    def test_eq(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, _ = _run(db, database_block, [self._uf(schema, "eq", "alice")])
        assert _ids(entries) == {e1.id}

    def test_is_empty(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, total = _run(db, database_block, [self._uf(schema, "is_empty")])
        assert total == 1 and _ids(entries) == {e3.id}

    def test_is_not_empty(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, total = _run(db, database_block, [self._uf(schema, "is_not_empty")])
        assert total == 2 and e3.id not in _ids(entries)

    def test_last_edited_by_same_logic(self, db, database_block):
        schema = _schema(db, database_block, "Edited by", "last_edited_by")
        e1 = _entry(db, database_block, pos=1.0)
        e2 = _entry(db, database_block, pos=2.0)
        _val(db, e1, schema, {"username": "alice"})
        entries, _ = _run(db, database_block,
            [_f(str(schema.id), "contains", "ali", schema_type="last_edited_by")])
        assert _ids(entries) == {e1.id}


# ─── Email / Phone / URL schema filters ───────────────────────────────────────


class TestFormattedTextSchemaFilters:

    @pytest.mark.parametrize("schema_type,stored_value,query_value", [
        ("email", "test@example.com", "example"),
        ("phone", "+49 89 12345",     "12345"),
        ("url",   "https://example.com/path", "example"),
    ])
    def test_contains(self, db, database_block, schema_type, stored_value, query_value):
        schema = _schema(db, database_block, "Contact", schema_type)
        e1 = _entry(db, database_block, pos=1.0)
        e2 = _entry(db, database_block, pos=2.0)
        _val(db, e1, schema, {"value": stored_value})
        entries, _ = _run(db, database_block,
            [_f(str(schema.id), "contains", query_value, schema_type=schema_type)])
        assert _ids(entries) == {e1.id}

    @pytest.mark.parametrize("schema_type", ["email", "phone", "url"])
    def test_is_empty(self, db, database_block, schema_type):
        schema = _schema(db, database_block, "Contact", schema_type)
        e1 = _entry(db, database_block, pos=1.0)
        e2 = _entry(db, database_block, pos=2.0)
        _val(db, e1, schema, {"value": "something"})
        entries, total = _run(db, database_block,
            [_f(str(schema.id), "is_empty", schema_type=schema_type)])
        assert total == 1 and _ids(entries) == {e2.id}

    @pytest.mark.parametrize("schema_type", ["email", "phone", "url"])
    def test_eq(self, db, database_block, schema_type):
        schema = _schema(db, database_block, "Contact", schema_type)
        e1 = _entry(db, database_block, pos=1.0)
        e2 = _entry(db, database_block, pos=2.0)
        _val(db, e1, schema, {"value": "alice@test.com"})
        _val(db, e2, schema, {"value": "bob@test.com"})
        entries, _ = _run(db, database_block,
            [_f(str(schema.id), "eq", "alice@test.com", schema_type=schema_type)])
        assert _ids(entries) == {e1.id}


# ─── Relation schema filters ───────────────────────────────────────────────────


class TestRelationSchemaFilters:

    @pytest.fixture
    def setup(self, db, database_block):
        schema = _schema(db, database_block, "Related", "relation",
                         config={"target_database_id": str(database_block.id),
                                 "direction": "unilateral"})
        e1 = _entry(db, database_block, pos=1.0)
        e2 = _entry(db, database_block, pos=2.0)
        e3 = _entry(db, database_block, pos=3.0)
        _val(db, e1, schema, {"related_ids": [str(e2.id)]})
        _val(db, e2, schema, {"related_ids": []})
        # e3 has no row
        return schema, e1, e2, e3

    def _rf(self, schema, op, value=""):
        return _f(str(schema.id), op, value, schema_type="relation")

    def test_is_not_empty_matches_entry_with_ids(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, _ = _run(db, database_block, [self._rf(schema, "is_not_empty")])
        assert e1.id in _ids(entries)
        assert e2.id not in _ids(entries)
        assert e3.id not in _ids(entries)

    def test_is_empty_matches_empty_array_and_no_row(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, _ = _run(db, database_block, [self._rf(schema, "is_empty")])
        assert e1.id not in _ids(entries)
        assert e2.id in _ids(entries)
        assert e3.id in _ids(entries)

    def test_contains_matches_entry_whose_relation_includes_target(self, db, database_block, setup):
        # e1 has related_ids=[e2.id], so filtering contains(e2) must return only e1.
        schema, e1, e2, e3 = setup
        entries, _ = _run(db, database_block, [self._rf(schema, "contains", str(e2.id))])
        assert e1.id in _ids(entries)
        assert e2.id not in _ids(entries)
        assert e3.id not in _ids(entries)

    def test_contains_with_unrelated_id_returns_empty(self, db, database_block, setup):
        # No entry has e1.id in its related_ids.
        schema, e1, e2, e3 = setup
        entries, _ = _run(db, database_block, [self._rf(schema, "contains", str(e1.id))])
        assert _ids(entries) == set()

    def test_not_contains_excludes_entry_with_matching_id(self, db, database_block, setup):
        # e1 links to e2 → not_contains(e2) must exclude e1 but include e2 and e3.
        schema, e1, e2, e3 = setup
        entries, _ = _run(db, database_block, [self._rf(schema, "not_contains", str(e2.id))])
        assert e1.id not in _ids(entries)
        assert e2.id in _ids(entries)
        assert e3.id in _ids(entries)

    def test_contains_empty_value_returns_none_clause(self, db, database_block, setup):
        # An empty value string must not crash – it produces no clause, so all
        # entries are returned (the filter is treated as a no-op).
        schema, e1, e2, e3 = setup
        entries, total = _run(db, database_block, [self._rf(schema, "contains", "")])
        assert total == 3


# ─── File schema filters ───────────────────────────────────────────────────────


class TestFileSchemaFilters:

    @pytest.fixture
    def setup(self, db, database_block):
        schema = _schema(db, database_block, "Attachment", "file")
        e1 = _entry(db, database_block, pos=1.0)
        e2 = _entry(db, database_block, pos=2.0)
        e3 = _entry(db, database_block, pos=3.0)
        _val(db, e1, schema, {"files": [{"name": "doc.pdf", "url": "/uploads/doc.pdf"}]})
        _val(db, e2, schema, {"files": []})
        # e3 has no row
        return schema, e1, e2, e3

    def _ff(self, schema, op):
        return _f(str(schema.id), op, schema_type="file")

    def test_is_not_empty_matches_entry_with_files(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, _ = _run(db, database_block, [self._ff(schema, "is_not_empty")])
        assert e1.id in _ids(entries)
        assert e2.id not in _ids(entries)
        assert e3.id not in _ids(entries)

    def test_is_empty_matches_empty_array_and_no_row(self, db, database_block, setup):
        schema, e1, e2, e3 = setup
        entries, _ = _run(db, database_block, [self._ff(schema, "is_empty")])
        assert e1.id not in _ids(entries)
        assert e2.id in _ids(entries)
        assert e3.id in _ids(entries)


# ─── Combined / AND filters ────────────────────────────────────────────────────


class TestCombinedFilters:

    def test_two_filters_are_anded(self, db, database_block):
        rank_schema = _schema(db, database_block, "Rank", "number")
        e1 = _entry(db, database_block, "Alpha",   pos=1.0)
        e2 = _entry(db, database_block, "Beta",    pos=2.0)
        e3 = _entry(db, database_block, "Charlie", pos=3.0)
        _val(db, e1, rank_schema, {"number": 1})
        _val(db, e2, rank_schema, {"number": 5})
        _val(db, e3, rank_schema, {"number": 10})
        filters = [
            _name_f("contains", "a"),
            _f(str(rank_schema.id), "gte", "5", schema_type="number"),
        ]
        entries, total = _run(db, database_block, filters)
        assert total == 2
        assert e1.id not in _ids(entries)
        assert e2.id in _ids(entries) and e3.id in _ids(entries)

    def test_filter_and_sort_combined(self, db, database_block):
        schema = _schema(db, database_block, "Rank", "number")
        e1 = _entry(db, database_block, pos=1.0)
        e2 = _entry(db, database_block, pos=2.0)
        e3 = _entry(db, database_block, pos=3.0)
        _val(db, e1, schema, {"number": 10})
        _val(db, e2, schema, {"number": 5})
        _val(db, e3, schema, {"number": 1})
        filters = [_f(str(schema.id), "gt", "1", schema_type="number")]
        sorts   = [repo.SortDescriptor(schema_id=str(schema.id), schema_type="number", direction="asc")]
        entries, total = _run(db, database_block, filters, sorts)
        assert total == 2
        assert entries[0].id == e2.id
        assert entries[1].id == e1.id

    def test_empty_filter_list_returns_all(self, db, database_block):
        for i in range(4):
            _entry(db, database_block, pos=float(i + 1))
        entries, total = _run(db, database_block, [])
        assert total == 4

    def test_unknown_schema_id_at_repo_level_finds_nothing(self, db, database_block):
        # At the REPOSITORY level, a FilterDescriptor with an unknown UUID and
        # a specified schema_type IS compiled into a SQL EXISTS that finds no rows.
        # Unknown-schema *skipping* happens at the ROUTER level, not here.
        _entry(db, database_block, "Alpha", pos=1.0)
        _entry(db, database_block, "Beta",  pos=2.0)
        f = _f(str(uuid.uuid4()), "eq", "something", schema_type="text")
        entries, total = _run(db, database_block, [f])
        assert total == 0

    def test_trashed_entries_never_included(self, db, database_block):
        active = _entry(db, database_block, "Active", pos=1.0)
        trashed = repo.create_block(
            db, type="page", position=2.0, parent_id=database_block.id,
            content={"title": "Trashed"}, state="trash",
        )
        db.commit()
        entries, total = _run(db, database_block, [])
        assert total == 1 and trashed.id not in _ids(entries)


# ─── Sort edge cases ───────────────────────────────────────────────────────────


class TestSortEdgeCases:

    def test_nulls_sorted_last_ascending(self, db, database_block):
        schema = _schema(db, database_block, "Rank", "number")
        e_null = _entry(db, database_block, pos=1.0)
        e_one  = _entry(db, database_block, pos=2.0)
        e_two  = _entry(db, database_block, pos=3.0)
        _val(db, e_one, schema, {"number": 1})
        _val(db, e_two, schema, {"number": 2})
        sorts = [repo.SortDescriptor(schema_id=str(schema.id), schema_type="number", direction="asc")]
        entries, _ = _run(db, database_block, [], sorts)
        assert entries[-1].id == e_null.id

    def test_position_is_stable_tiebreaker(self, db, database_block):
        schema = _schema(db, database_block, "Rank", "number")
        e1 = _entry(db, database_block, pos=1.0)
        e2 = _entry(db, database_block, pos=2.0)
        _val(db, e1, schema, {"number": 42})
        _val(db, e2, schema, {"number": 42})
        sorts = [repo.SortDescriptor(schema_id=str(schema.id), schema_type="number", direction="asc")]
        entries, _ = _run(db, database_block, [], sorts)
        assert entries[0].id == e1.id and entries[1].id == e2.id

    def test_sort_by_name_asc(self, db, database_block):
        _entry(db, database_block, "Zeta",  pos=1.0)
        _entry(db, database_block, "Alpha", pos=2.0)
        _entry(db, database_block, "Mu",    pos=3.0)
        sorts = [repo.SortDescriptor(schema_id="__name__", schema_type=None, direction="asc")]
        entries, _ = _run(db, database_block, [], sorts)
        titles = [e.content["title"] for e in entries]
        assert titles == sorted(titles, key=str.lower)

    def test_sort_by_name_desc(self, db, database_block):
        _entry(db, database_block, "Zeta",  pos=1.0)
        _entry(db, database_block, "Alpha", pos=2.0)
        sorts = [repo.SortDescriptor(schema_id="__name__", schema_type=None, direction="desc")]
        entries, _ = _run(db, database_block, [], sorts)
        titles = [e.content["title"] for e in entries]
        assert titles == sorted(titles, key=str.lower, reverse=True)


# ─── Date sort tests (#70) ────────────────────────────────────────────────────


class TestDateSort:
    """
    Sort tests for date, created_time and last_edited_time schema types.

    Issue #70: dates were being compared as plain strings with no guarantee of
    chronological order.  The sort must compare ISO-8601 values chronologically,
    NOT alphabetically by the display format (DD.MM.YYYY would put "17.09.2189"
    before "18.03.2000" because "17" < "18").
    """

    # ── date type ─────────────────────────────────────────────────────────────

    def test_sort_by_date_asc_is_chronological(self, db, database_block):
        """#70: 18.03.2000 (2000-03-18) must sort BEFORE 17.09.2189 (2189-09-17)."""
        schema  = _schema(db, database_block, "Due", "date")
        e_early = _entry(db, database_block, pos=1.0)
        e_late  = _entry(db, database_block, pos=2.0)
        _val(db, e_early, schema, {"start": "2000-03-18", "end": "2000-03-18"})
        _val(db, e_late,  schema, {"start": "2189-09-17", "end": "2189-09-17"})
        sorts = [repo.SortDescriptor(schema_id=str(schema.id), schema_type="date", direction="asc")]
        entries, _ = _run(db, database_block, [], sorts)
        assert entries[0].id == e_early.id, "year 2000 must come before year 2189 (asc)"
        assert entries[1].id == e_late.id

    def test_sort_by_date_desc_is_reverse_chronological(self, db, database_block):
        schema  = _schema(db, database_block, "Due", "date")
        e_early = _entry(db, database_block, pos=1.0)
        e_late  = _entry(db, database_block, pos=2.0)
        _val(db, e_early, schema, {"start": "2000-03-18", "end": "2000-03-18"})
        _val(db, e_late,  schema, {"start": "2189-09-17", "end": "2189-09-17"})
        sorts = [repo.SortDescriptor(schema_id=str(schema.id), schema_type="date", direction="desc")]
        entries, _ = _run(db, database_block, [], sorts)
        assert entries[0].id == e_late.id,  "year 2189 must come first (desc)"
        assert entries[1].id == e_early.id

    def test_sort_by_date_multiple_years(self, db, database_block):
        """Chronological order across a wider spread of years."""
        schema = _schema(db, database_block, "Due", "date")
        dates  = ["2025-06-15", "1999-12-31", "2189-09-17", "2000-03-18", "2025-01-01"]
        entries_map: dict[str, object] = {}
        for i, d in enumerate(dates):
            e = _entry(db, database_block, pos=float(i + 1))
            _val(db, e, schema, {"start": d, "end": d})
            entries_map[d] = e
        sorts = [repo.SortDescriptor(schema_id=str(schema.id), schema_type="date", direction="asc")]
        result, _ = _run(db, database_block, [], sorts)
        result_ids = [e.id for e in result]
        expected_order = sorted(dates)   # ISO strings sort chronologically
        expected_ids   = [entries_map[d].id for d in expected_order]
        assert result_ids == expected_ids

    def test_sort_by_date_with_time_component(self, db, database_block):
        """All-day entries (no T) sort before timed entries on the same day."""
        schema    = _schema(db, database_block, "Due", "date")
        e_allday  = _entry(db, database_block, pos=1.0)
        e_morning = _entry(db, database_block, pos=2.0)
        e_evening = _entry(db, database_block, pos=3.0)
        _val(db, e_allday,  schema, {"start": "2024-06-15",       "end": "2024-06-15"})
        _val(db, e_morning, schema, {"start": "2024-06-15T09:00", "end": "2024-06-15T09:00"})
        _val(db, e_evening, schema, {"start": "2024-06-15T18:00", "end": "2024-06-15T18:00"})
        sorts = [repo.SortDescriptor(schema_id=str(schema.id), schema_type="date", direction="asc")]
        entries, _ = _run(db, database_block, [], sorts)
        assert entries[0].id == e_allday.id,  "all-day before timed (same calendar day)"
        assert entries[1].id == e_morning.id, "09:00 before 18:00"
        assert entries[2].id == e_evening.id

    def test_sort_by_date_nulls_sorted_last_asc(self, db, database_block):
        schema = _schema(db, database_block, "Due", "date")
        e_null = _entry(db, database_block, pos=1.0)
        e_date = _entry(db, database_block, pos=2.0)
        _val(db, e_date, schema, {"start": "2024-01-15", "end": "2024-01-15"})
        # e_null has no PropertyValue row at all
        sorts = [repo.SortDescriptor(schema_id=str(schema.id), schema_type="date", direction="asc")]
        entries, _ = _run(db, database_block, [], sorts)
        assert entries[-1].id == e_null.id

    def test_sort_by_date_nulls_sorted_last_desc(self, db, database_block):
        schema = _schema(db, database_block, "Due", "date")
        e_null  = _entry(db, database_block, pos=1.0)
        e_early = _entry(db, database_block, pos=2.0)
        e_late  = _entry(db, database_block, pos=3.0)
        _val(db, e_early, schema, {"start": "2020-01-01", "end": "2020-01-01"})
        _val(db, e_late,  schema, {"start": "2030-12-31", "end": "2030-12-31"})
        sorts = [repo.SortDescriptor(schema_id=str(schema.id), schema_type="date", direction="desc")]
        entries, _ = _run(db, database_block, [], sorts)
        assert entries[-1].id == e_null.id, "no-value entries must be last even in desc"

    # ── created_time / last_edited_time ───────────────────────────────────────

    def test_sort_by_created_time_asc(self, db, database_block):
        schema = _schema(db, database_block, "Created", "created_time")
        e1 = _entry(db, database_block, pos=1.0)
        e2 = _entry(db, database_block, pos=2.0)
        e3 = _entry(db, database_block, pos=3.0)
        _val(db, e1, schema, {"datetime": "2024-01-01T10:00:00+00:00"})
        _val(db, e2, schema, {"datetime": "2024-06-15T14:30:00+00:00"})
        _val(db, e3, schema, {"datetime": "2023-12-31T23:59:59+00:00"})
        sorts = [repo.SortDescriptor(schema_id=str(schema.id), schema_type="created_time", direction="asc")]
        entries, _ = _run(db, database_block, [], sorts)
        assert entries[0].id == e3.id, "2023-12-31 must come first"
        assert entries[1].id == e1.id
        assert entries[2].id == e2.id

    def test_sort_by_last_edited_time_desc(self, db, database_block):
        schema = _schema(db, database_block, "Edited", "last_edited_time")
        e1 = _entry(db, database_block, pos=1.0)
        e2 = _entry(db, database_block, pos=2.0)
        _val(db, e1, schema, {"datetime": "2024-01-01T08:00:00+00:00"})
        _val(db, e2, schema, {"datetime": "2024-06-01T12:00:00+00:00"})
        sorts = [repo.SortDescriptor(schema_id=str(schema.id), schema_type="last_edited_time", direction="desc")]
        entries, _ = _run(db, database_block, [], sorts)
        assert entries[0].id == e2.id, "more recent edit must come first in desc"
        assert entries[1].id == e1.id


# ─── Filter groups ────────────────────────────────────────────────────────────


class TestFilterGroups:
    """
    Tests for multi-group filter logic.

    Verifies:
      - OR conjunction within a group (any condition matches)
      - AND conjunction within a group (all conditions must match)
      - Multiple groups are always ANDed together at the query level
      - Empty filter_groups list returns all entries
      - Single-group behaviour is identical to the legacy flat-filter path
    """

    @pytest.fixture
    def setup(self, db, database_block):
        rank_schema = _schema(db, database_block, "Rank", "number")
        faction_schema = _schema(db, database_block, "Faction", "text")
        e1 = _entry(db, database_block, "Alice", pos=1.0)
        e2 = _entry(db, database_block, "Bob",   pos=2.0)
        e3 = _entry(db, database_block, "Carol", pos=3.0)
        e4 = _entry(db, database_block, "Dave",  pos=4.0)
        _val(db, e1, rank_schema,    {"number": 1})
        _val(db, e2, rank_schema,    {"number": 2})
        _val(db, e3, rank_schema,    {"number": 3})
        _val(db, e4, rank_schema,    {"number": 4})
        _val(db, e1, faction_schema, {"text": "empire"})
        _val(db, e2, faction_schema, {"text": "empire"})
        _val(db, e3, faction_schema, {"text": "rebel"})
        _val(db, e4, faction_schema, {"text": "rebel"})
        return rank_schema, faction_schema, e1, e2, e3, e4

    def test_no_groups_returns_all(self, db, database_block, setup):
        _, _, e1, e2, e3, e4 = setup
        entries, total = _run_groups(db, database_block, [])
        assert total == 4

    def test_single_and_group_behaves_like_flat_filters(self, db, database_block, setup):
        rank_schema, faction_schema, e1, e2, e3, e4 = setup
        group = _and_group(
            _f(str(rank_schema.id),    "gt",  "1", schema_type="number"),
            _f(str(faction_schema.id), "eq", "empire", schema_type="text"),
        )
        entries, total = _run_groups(db, database_block, [group])
        # rank > 1 AND faction = empire → only Bob (rank=2, empire)
        assert total == 1
        assert _ids(entries) == {e2.id}

    def test_or_group_matches_any_condition(self, db, database_block, setup):
        rank_schema, faction_schema, e1, e2, e3, e4 = setup
        # rank = 1 OR rank = 4
        group = _or_group(
            _f(str(rank_schema.id), "eq", "1", schema_type="number"),
            _f(str(rank_schema.id), "eq", "4", schema_type="number"),
        )
        entries, total = _run_groups(db, database_block, [group])
        assert total == 2
        assert _ids(entries) == {e1.id, e4.id}

    def test_or_group_with_name_and_schema(self, db, database_block, setup):
        rank_schema, faction_schema, e1, e2, e3, e4 = setup
        # name contains "Alice" OR faction = "rebel"
        group = _or_group(
            _name_f("contains", "Alice"),
            _f(str(faction_schema.id), "eq", "rebel", schema_type="text"),
        )
        entries, total = _run_groups(db, database_block, [group])
        # Alice (name), Carol (rebel), Dave (rebel)
        assert total == 3
        assert _ids(entries) == {e1.id, e3.id, e4.id}

    def test_two_and_groups_are_anded_together(self, db, database_block, setup):
        rank_schema, faction_schema, e1, e2, e3, e4 = setup
        # Group 1 (AND): rank > 1
        # Group 2 (AND): faction = empire
        # Result: rank > 1 AND faction = empire → Bob only
        g1 = _and_group(_f(str(rank_schema.id),    "gt", "1",      schema_type="number"))
        g2 = _and_group(_f(str(faction_schema.id), "eq", "empire", schema_type="text"))
        entries, total = _run_groups(db, database_block, [g1, g2])
        assert total == 1
        assert _ids(entries) == {e2.id}

    def test_or_group_and_and_group_combined(self, db, database_block, setup):
        rank_schema, faction_schema, e1, e2, e3, e4 = setup
        # Group 1 (OR):  rank = 1 OR rank = 2   → Alice, Bob
        # Group 2 (AND): faction = empire        → Alice, Bob
        # Combined (groups ANDed): Alice, Bob
        g1 = _or_group(
            _f(str(rank_schema.id), "eq", "1", schema_type="number"),
            _f(str(rank_schema.id), "eq", "2", schema_type="number"),
        )
        g2 = _and_group(_f(str(faction_schema.id), "eq", "empire", schema_type="text"))
        entries, total = _run_groups(db, database_block, [g1, g2])
        assert total == 2
        assert _ids(entries) == {e1.id, e2.id}

    def test_or_group_with_no_match_returns_empty(self, db, database_block, setup):
        rank_schema, _, e1, e2, e3, e4 = setup
        # rank = 99 OR rank = 100 → nobody
        group = _or_group(
            _f(str(rank_schema.id), "eq", "99",  schema_type="number"),
            _f(str(rank_schema.id), "eq", "100", schema_type="number"),
        )
        entries, total = _run_groups(db, database_block, [group])
        assert total == 0

    def test_group_with_single_filter_works(self, db, database_block, setup):
        rank_schema, _, e1, e2, e3, e4 = setup
        group = _or_group(_f(str(rank_schema.id), "lte", "2", schema_type="number"))
        entries, total = _run_groups(db, database_block, [group])
        assert total == 2
        assert _ids(entries) == {e1.id, e2.id}

    def test_three_groups_all_anded(self, db, database_block, setup):
        rank_schema, faction_schema, e1, e2, e3, e4 = setup
        # rank >= 2  AND  rank <= 3  AND  faction = rebel
        # rank 2..3 = Bob(empire), Carol(rebel) → only Carol
        g1 = _and_group(_f(str(rank_schema.id),    "gte", "2",     schema_type="number"))
        g2 = _and_group(_f(str(rank_schema.id),    "lte", "3",     schema_type="number"))
        g3 = _and_group(_f(str(faction_schema.id), "eq",  "rebel", schema_type="text"))
        entries, total = _run_groups(db, database_block, [g1, g2, g3])
        assert total == 1
        assert _ids(entries) == {e3.id}
