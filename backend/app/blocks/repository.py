"""
Block repository.

Thin data-access layer: all SQL reads and writes go through these functions.
No business logic lives here; that belongs in the service layer.
"""
import calendar
import uuid
from dataclasses import dataclass, field as dc_field
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import String, and_, case, cast, false, func, or_, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session, aliased

from app.blocks import options
from app.blocks.models import (
    Block,
    BlockEvent,
    BlockPreference,
    PropertySchema,
    PropertyValue,
)


# ─── Query descriptors ────────────────────────────────────────────────────────


@dataclass
class FilterDescriptor:
    """
    Resolved filter descriptor used by query_entries().

    schema_id     – '__name__' for the title column, or a schema UUID string.
    schema_type   – schema.type (e.g. 'text', 'number', 'select', …), or None
                    for the name column.
    schema_config – schema.config dict (needed for multi-select mode detection),
                    or None.
    operator      – one of the FilterOperator literals from the frontend.
    value         – serialised comparison value; empty for valueless operators.
    date_mode     – 'exact' | 'today' | 'relative'; relevant for date schemas
                    with point-comparison operators.
    date_offset   – day offset when date_mode == 'relative'.
    value2        – upper bound for the 'between' operator (ISO date string).
    """
    schema_id: str
    schema_type: Optional[str]
    schema_config: Optional[dict]
    operator: str
    value: str
    date_mode: Optional[str] = None
    date_offset: Optional[int] = None
    formula_result_type: Optional[str] = None
    value2: Optional[str] = None


@dataclass
class SortDescriptor:
    """
    Resolved sort descriptor used by query_entries().

    schema_id   – '__name__' for the title column, or a schema UUID string.
    schema_type – schema.type, or None for the name column.
    schema_config – schema.config dict (needed to read a rollup's aggregation
                    function so its result is sorted with the correct datatype),
                    or None.
    direction   – 'asc' or 'desc'.
    """
    schema_id: str
    schema_type: Optional[str]
    schema_config: Optional[dict] = None
    direction: str = 'asc'


@dataclass
class FilterGroupDescriptor:
    """
    A group of filter conditions with a shared conjunction.

    conjunction – 'and' (all conditions must match) or 'or' (any must match).
    filters     – the individual filter descriptors in this group.

    Groups themselves are always ANDed together at the query level.
    """
    conjunction: str = 'and'
    filters: list = dc_field(default_factory=list)  # list[FilterDescriptor]


# ─── Filter resolution (shared by query endpoint & automation engine) ─────────


def resolve_filter_descriptor(
    schema_map: dict[str, PropertySchema],
    *,
    schema_id: str,
    operator: str,
    value: str = '',
    date_mode: Optional[str] = None,
    date_offset: Optional[int] = None,
    formula_result_type: Optional[str] = None,
    value2: Optional[str] = None,
) -> Optional[FilterDescriptor]:
    """
    Resolve a single raw filter condition into a FilterDescriptor.

    *schema_map* maps a schema-UUID string to its PropertySchema.  The special
    '__name__' schema_id (the entry title) resolves with no type/config.  A
    schema_id that is not present in *schema_map* yields None so the caller can
    skip the (stale) condition.

    This is the single resolution path shared by the database query endpoint
    (database_entries_router.query_entries) and the automation bulk-action engine
    (automations_engine._handle_bulk_upsert_value), so a stored filter spec is
    interpreted identically no matter which subsystem evaluates it.

    The formula_result_type is only meaningful for formula schemas and is forced
    to None for every other type, matching the query endpoint's prior behaviour.
    """
    if schema_id == '__name__':
        schema_type = None
        schema_config = None
    else:
        schema = schema_map.get(schema_id)
        if schema is None:
            return None
        schema_type = schema.type
        schema_config = schema.config
    return FilterDescriptor(
        schema_id=schema_id,
        schema_type=schema_type,
        schema_config=schema_config,
        operator=operator,
        value=value,
        date_mode=date_mode,
        date_offset=date_offset,
        formula_result_type=formula_result_type if schema_type == 'formula' else None,
        value2=value2,
    )


# ─── Query helpers ────────────────────────────────────────────────────────────

_PRESET_OPERATORS: frozenset[str] = frozenset({
    'past_week', 'past_month', 'past_year',
    'this_week', 'next_week', 'next_month', 'next_year',
})


def _preset_range_iso(operator: str) -> Optional[tuple[str, str]]:
    """
    Return (start_iso, end_iso) date strings for a preset date-range operator,
    computed relative to today.  Returns None for non-preset operators.

    ISO date strings sort lexicographically, so string comparison in SQL is
    equivalent to chronological comparison for YYYY-MM-DD format.
    """
    today = date.today()

    def iso(d: date) -> str:
        return d.isoformat()

    if operator == 'past_week':
        return iso(today - timedelta(days=7)), iso(today)
    if operator == 'past_month':
        m = today.month - 1 or 12
        y = today.year - (1 if today.month == 1 else 0)
        d = min(today.day, calendar.monthrange(y, m)[1])
        return iso(date(y, m, d)), iso(today)
    if operator == 'past_year':
        try:
            start = today.replace(year=today.year - 1)
        except ValueError:
            start = today.replace(year=today.year - 1, day=28)
        return iso(start), iso(today)
    if operator == 'this_week':
        monday = today - timedelta(days=today.weekday())
        return iso(monday), iso(monday + timedelta(days=6))
    if operator == 'next_week':
        monday = today - timedelta(days=today.weekday()) + timedelta(weeks=1)
        return iso(monday), iso(monday + timedelta(days=6))
    if operator == 'next_month':
        m = today.month % 12 + 1
        y = today.year + (1 if today.month == 12 else 0)
        last = calendar.monthrange(y, m)[1]
        return iso(today), iso(date(y, m, last))
    if operator == 'next_year':
        try:
            end = today.replace(year=today.year + 1)
        except ValueError:
            end = today.replace(year=today.year + 1, day=28)
        return iso(today), iso(end)
    return None


def _ref_date_iso(
    date_mode: Optional[str],
    date_offset: Optional[int],
    value: str,
) -> Optional[str]:
    """Return the ISO reference date string for a point-comparison date filter."""
    today = date.today()
    if date_mode == 'today':
        return today.isoformat()
    if date_mode == 'relative':
        offset = date_offset or 0
        return (today + timedelta(days=offset)).isoformat()
    # 'exact' or absent: use the value string directly
    return value if value else None


# Collation for sorting text (migration 0029): digit runs compare as numbers
# and letters as in a dictionary, so "1, 2, 10" and "a, A, b" instead of the
# byte order "1, 10, 2" and "A, B, a".
NATURAL_SORT_COLLATION = 'natural_sort'


def _natural(expr):
    """*expr* sorted in natural order (see ``NATURAL_SORT_COLLATION``)."""
    return expr.collate(NATURAL_SORT_COLLATION)


# Rollup aggregation functions whose ``result`` is a numeric scalar. Sorting a
# rollup column by one of these must cast ``result`` to a float so that values
# order numerically (10 < 20 < 100) rather than lexicographically. Every other
# rollup function yields a date string, raw scalar, list, or map and is sorted
# as text, where ISO-8601 date strings still order chronologically. Kept in
# sync with the rollup function catalogue in app/blocks/computed.py.
_NUMERIC_ROLLUP_FUNCTIONS: frozenset[str] = frozenset({
    'count', 'count_values', 'count_empty', 'count_not_empty', 'count_unique',
    'percent_empty', 'percent_not_empty', 'percent_checked', 'percent_unchecked',
    'checked', 'sum', 'avg', 'median', 'min', 'max', 'range',
})


def _select_sort_expression(option_expr, schema_config: Optional[dict]):
    """
    Sort key of a single-select value.

    Values store option ids, which say nothing about order. By default the
    options sort alphabetically by label, ignoring case, in natural order
    (numbers within a label compare as numbers); with the property's
    manual-order toggle on they sort in the order the options are configured.
    Ids without an option, and empty values, sort last.
    """
    choices = options.option_list('select', schema_config)
    if not choices:
        return _natural(option_expr)
    if (schema_config or {}).get(options.MANUAL_SORT_KEY) is True:
        whens = {o['id']: index for index, o in enumerate(choices)}
        return case(whens, value=option_expr, else_=None)
    whens = {o['id']: o['label'].lower() for o in choices}
    return _natural(case(whens, value=option_expr, else_=None))


def _value_key(schema_type: str) -> str:
    """Return the JSONB field name that holds the filterable scalar for a type."""
    if schema_type == 'number':
        return 'number'
    if schema_type == 'checkbox':
        return 'checked'
    if schema_type == 'select':
        return 'option'   # single-select; multi-select is handled separately
    if schema_type == 'date':
        return 'start'
    if schema_type in ('created_time', 'last_edited_time'):
        return 'datetime'
    if schema_type in ('email', 'phone', 'url'):
        return 'value'
    if schema_type == 'id':
        return 'id_value'
    if schema_type in ('created_by', 'last_edited_by'):
        return 'username'
    if schema_type == 'formula':
        return 'result'
    return 'text'   # text and all other fallback types


# Escape character for LIKE patterns built from user input. Declared explicitly
# on every LIKE so the pattern never depends on the database's default escape
# behaviour.
_LIKE_ESCAPE = '\\'


def _escape_like(value: str) -> str:
    """
    Escape the LIKE wildcards in *value* so it matches literally.

    Without this a search for "50%" matches anything starting with "50", and
    every "_" matches any single character. The escape character itself is
    escaped first so that a literal backslash survives as well.
    """
    return (
        value.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace('%', _LIKE_ESCAPE + '%')
        .replace('_', _LIKE_ESCAPE + '_')
    )


def _like_contains(expr, value: str):
    """``expr LIKE '%value%'`` with *value* matched literally."""
    return expr.like(f'%{_escape_like(value)}%', escape=_LIKE_ESCAPE)


def _like_starts_with(expr, value: str):
    """``expr LIKE 'value%'`` with *value* matched literally."""
    return expr.like(f'{_escape_like(value)}%', escape=_LIKE_ESCAPE)


def _like_ends_with(expr, value: str):
    """``expr LIKE '%value'`` with *value* matched literally."""
    return expr.like(f'%{_escape_like(value)}', escape=_LIKE_ESCAPE)


def _text_operator_clause(expr, operator: str, value: str):
    """
    Return a SQLAlchemy boolean clause for a text operator applied directly
    to *expr* (used for the name / title column).

    Negative operators (neq, not_contains, ends_with negation) must guard
    against NULL: SQL evaluates NULL != 'x' as NULL (falsy), which would
    silently exclude rows with no title.  We treat NULL as an empty string
    by wrapping with OR IS NULL.

    Returns None for unknown operators so callers can skip them cleanly.
    """
    lower_expr = func.lower(expr)
    lv = value.lower()
    if operator == 'is_empty':
        return or_(expr.is_(None), expr == '')
    if operator == 'is_not_empty':
        return and_(expr.is_not(None), expr != '')
    if operator == 'contains':
        return and_(expr.is_not(None), _like_contains(lower_expr, lv))
    if operator == 'not_contains':
        return or_(expr.is_(None), ~_like_contains(lower_expr, lv))
    if operator == 'starts_with':
        return and_(expr.is_not(None), _like_starts_with(lower_expr, lv))
    if operator == 'ends_with':
        return and_(expr.is_not(None), _like_ends_with(lower_expr, lv))
    if operator == 'eq':
        return and_(expr.is_not(None), lower_expr == lv)
    if operator == 'neq':
        return or_(expr.is_(None), lower_expr != lv)
    return None


class _CorrelatedValue:
    """
    The stored value of one property for the entry being filtered.

    Every clause built from it is a correlated subquery on its own
    ``PropertyValue`` alias, so filters stay independent of each other and no
    join fanout occurs between them.
    """

    def __init__(self, schema_uuid: uuid.UUID) -> None:
        self.pv = aliased(PropertyValue)
        self.schema_uuid = schema_uuid

    @property
    def value(self):
        """The JSONB value column."""
        return self.pv.value

    def base(self):
        """A fresh correlated subquery base: the row of this property, if any."""
        return (
            select(1)
            .where(self.pv.page_id == Block.id)
            .where(self.pv.property_schema_id == self.schema_uuid)
        )


def _not_empty_array(cv: _CorrelatedValue, key: str):
    """Subquery base: the value holds a non-empty JSON array under *key*."""
    arr_str = cv.value[key].as_string()
    return (
        cv.base()
        .where(cv.value.is_not(None))
        .where(arr_str.is_not(None))
        .where(arr_str != '[]')
    )


def _emptiness_clause(f: FilterDescriptor, schema_type: str, is_multi: bool, cv: _CorrelatedValue):
    """``is_empty`` / ``is_not_empty`` for every property type."""
    if schema_type == 'relation':
        # A plain relation is non-empty with a non-empty related_ids list, a
        # timeline relation with a non-trivial _timeline (its pool-based value).
        timeline_str = cv.value['_timeline'].as_string()
        timeline_not_empty = (
            cv.base()
            .where(cv.value.is_not(None))
            .where(timeline_str.is_not(None))
            .where(timeline_str != '{}')
        )
        not_empty = or_(
            _not_empty_array(cv, 'related_ids').exists(),
            timeline_not_empty.exists(),
        )
    elif is_multi:
        not_empty = _not_empty_array(cv, 'options').exists()
    elif schema_type == 'file':
        not_empty = _not_empty_array(cv, 'files').exists()
    else:
        val_str = cv.value[_value_key(schema_type)].as_string()
        not_empty = (
            cv.base()
            .where(cv.value.is_not(None))
            .where(val_str.is_not(None))
            .where(val_str != '')
            .exists()
        )
    return (~not_empty) if f.operator == 'is_empty' else not_empty


def _date_clause(f: FilterDescriptor, cv: _CorrelatedValue, raw_expr, date_expr):
    """
    Date operators on a stored ISO string *raw_expr*, compared through
    *date_expr* (the date part of it).

    Presets cover a range relative to today, ``between`` an explicit inclusive
    range, and the point comparisons a reference date from ``date_mode``.
    """
    if f.operator in _PRESET_OPERATORS:
        preset = _preset_range_iso(f.operator)
        if preset is None:
            return None
        start_iso, end_iso = preset
        return (
            cv.base()
            .where(raw_expr.is_not(None))
            .where(date_expr >= start_iso)
            .where(date_expr <= end_iso)
            .exists()
        )

    if f.operator == 'between':
        start_iso = f.value or None
        end_iso = f.value2 or None
        if not start_iso or not end_iso:
            # An incomplete range must not pass every entry through; a
            # guaranteed-false expression excludes the rows instead.
            return false()
        return (
            cv.base()
            .where(raw_expr.is_not(None))
            .where(date_expr >= start_iso)
            .where(date_expr <= end_iso)
            .exists()
        )

    ref = _ref_date_iso(f.date_mode, f.date_offset, f.value)
    if not ref:
        return None
    op_map = {
        'eq': date_expr == ref,
        'gt': date_expr > ref,
        'gte': date_expr >= ref,
        'lt': date_expr < ref,
        'lte': date_expr <= ref,
    }
    cond = op_map.get(f.operator)
    if cond is None:
        return None
    return cv.base().where(raw_expr.is_not(None)).where(cond).exists()


def _date_property_clause(f: FilterDescriptor, schema_type: str, cv: _CorrelatedValue):
    """Date operators on a date property or a creation / edit timestamp."""
    date_key = 'start' if schema_type == 'date' else 'datetime'
    raw_expr = cv.value[date_key].as_string()
    # Timestamps carry a time part (YYYY-MM-DDTHH:…); compare their date prefix.
    date_expr = func.substr(raw_expr, 1, 10) if schema_type != 'date' else raw_expr
    return _date_clause(f, cv, raw_expr, date_expr)


def _relation_contains_clause(f: FilterDescriptor, cv: _CorrelatedValue):
    """
    ``contains`` / ``not_contains`` of one entry id, in a plain relation's
    related_ids and anywhere in a timeline relation's _timeline. Matches the
    quoted id in the JSON text, so no database-specific operators are needed.
    """
    if not f.value:
        return None
    needle = f'%"{f.value}"%'
    has_id_plain = (
        cv.base()
        .where(cv.value.is_not(None))
        .where(cv.value['related_ids'].as_string().like(needle))
    )
    has_id_timeline = (
        cv.base()
        .where(cv.value.is_not(None))
        .where(cv.value['_timeline'].as_string().like(needle))
    )
    has_id = or_(has_id_plain.exists(), has_id_timeline.exists())
    return has_id if f.operator == 'contains' else ~has_id


def _multi_select_clause(f: FilterDescriptor, cv: _CorrelatedValue):
    """``contains`` / ``not_contains`` of one option of a multi-select."""
    if not f.value:
        # No option selected in the filter panel: a no-op, so the view is not
        # cleared and "create with filter" does not make new rows disappear
        # at once. The relation branch has the same guard.
        return None
    options_str = cv.value['options'].as_string()
    # lower() on both sides makes the match case-insensitive in PostgreSQL,
    # where LIKE is case-sensitive. The value is wrapped in its JSON quotes so
    # that only a whole option matches.
    option_match = _like_contains(func.lower(options_str), f'"{f.value.lower()}"')
    if f.operator == 'contains':
        return cv.base().where(option_match).exists()
    if f.operator == 'not_contains':
        return ~cv.base().where(option_match).exists()
    return None


def _number_clause(f: FilterDescriptor, cv: _CorrelatedValue, num_expr):
    """Numeric comparisons on *num_expr*."""
    try:
        num_val = float(f.value)
    except (ValueError, TypeError):
        return None

    # neq uses NOT EXISTS so that entries with no value also satisfy "not
    # equal". All positive operators require the value to exist.
    if f.operator == 'neq':
        return ~cv.base().where(num_expr.is_not(None)).where(num_expr == num_val).exists()

    op_map = {
        'eq':  num_expr == num_val,
        'gt':  num_expr > num_val,
        'gte': num_expr >= num_val,
        'lt':  num_expr < num_val,
        'lte': num_expr <= num_val,
    }
    cond = op_map.get(f.operator)
    if cond is None:
        return None
    return cv.base().where(num_expr.is_not(None)).where(cond).exists()


def _boolean_clause(f: FilterDescriptor, cv: _CorrelatedValue, bool_expr):
    """
    ``eq`` / ``neq`` on *bool_expr*.

    The false state has two representations: no row at all (a checkbox that
    was never toggled) and a row holding false. Only a row holding true is
    true, so false is "no row holding true". ``''`` and ``'false'`` both ask
    for false. The JSON value is compared as a boolean rather than as the
    strings ``->>`` yields.
    """
    has_true = cv.base().where(bool_expr == True).exists()  # noqa: E712
    wants_true = f.value == 'true'
    if f.operator == 'eq':
        return has_true if wants_true else ~has_true
    if f.operator == 'neq':
        return (~has_true) if wants_true else has_true
    return None


def _text_clause(f: FilterDescriptor, cv: _CorrelatedValue, str_expr):
    """
    Text operators on the lower-cased string *str_expr*, case-insensitive.

    Negative operators use NOT EXISTS so entries with no value row (or a null
    value field) satisfy them, consistent with the frontend treating missing
    values as empty strings. Only the subquery of the requested operator is
    built.
    """
    lv = f.value.lower()
    if f.operator == 'not_contains':
        return ~cv.base().where(_like_contains(str_expr, lv)).exists()
    if f.operator == 'neq':
        return ~cv.base().where(str_expr == lv).exists()
    if f.operator == 'contains':
        return cv.base().where(_like_contains(str_expr, lv)).exists()
    if f.operator == 'starts_with':
        return cv.base().where(_like_starts_with(str_expr, lv)).exists()
    if f.operator == 'ends_with':
        return cv.base().where(_like_ends_with(str_expr, lv)).exists()
    if f.operator == 'eq':
        return cv.base().where(str_expr == lv).exists()
    return None


def _formula_clause(f: FilterDescriptor, cv: _CorrelatedValue):
    """A formula filter, compared as the formula's result type."""
    raw_expr = cv.value['result']
    result_type = f.formula_result_type or 'text'
    if result_type == 'number':
        return _number_clause(f, cv, raw_expr.as_float())
    if result_type == 'boolean':
        return _boolean_clause(f, cv, raw_expr.as_boolean())
    if result_type == 'date':
        raw_str = raw_expr.as_string()
        return _date_clause(f, cv, raw_str, func.substr(raw_str, 1, 10))
    return _text_clause(f, cv, func.lower(raw_expr.as_string()))


def _build_filter_clause(f: FilterDescriptor):
    """
    Return a SQLAlchemy WHERE expression for a single FilterDescriptor.

    Name-column filters operate directly on Block.content['title'].
    Schema-column filters use correlated EXISTS subqueries on PropertyValue
    so that each filter is independent and no cross-filter JOIN fanout occurs.

    The clause is chosen by operator and type, in this order: emptiness for
    every type; dates and timestamps; relation membership; multi-select
    membership; numbers and ids; checkboxes; formulas by result type; text for
    everything else (text, single select, email, phone, url, created_by,
    last_edited_by).

    Returns None if the filter is malformed or the operator is unrecognised;
    callers should skip None results.
    """
    if f.schema_id == '__name__':
        title_expr = Block.content['title'].as_string()
        return _text_operator_clause(title_expr, f.operator, f.value)

    try:
        schema_uuid = uuid.UUID(f.schema_id)
    except ValueError:
        return None

    schema_type = f.schema_type or 'text'
    is_multi = (
        schema_type == 'select' and
        isinstance(f.schema_config, dict) and
        f.schema_config.get('mode') == 'multiple'
    )
    cv = _CorrelatedValue(schema_uuid)

    if f.operator in ('is_empty', 'is_not_empty'):
        return _emptiness_clause(f, schema_type, is_multi, cv)
    if schema_type in ('date', 'created_time', 'last_edited_time'):
        return _date_property_clause(f, schema_type, cv)
    if schema_type == 'relation' and f.operator in ('contains', 'not_contains'):
        return _relation_contains_clause(f, cv)
    if is_multi:
        return _multi_select_clause(f, cv)
    if schema_type in ('number', 'id'):
        num_key = 'number' if schema_type == 'number' else 'id_value'
        return _number_clause(f, cv, cv.value[num_key].as_float())
    if schema_type == 'checkbox':
        return _boolean_clause(f, cv, cv.value['checked'].as_boolean())
    if schema_type == 'formula':
        return _formula_clause(f, cv)
    return _text_clause(f, cv, func.lower(cv.value[_value_key(schema_type)].as_string()))


# ─── Server-side entry query ──────────────────────────────────────────────────


def query_entries(
    db: Session,
    database_id: uuid.UUID,
    filter_groups: list[FilterGroupDescriptor],
    sorts: list[SortDescriptor],
    limit: int = 1000,
    offset: int = 0,
) -> tuple[list[Block], int]:
    """
    Return (entries, total_count) for *database_id* with server-side
    filtering, sorting, and pagination.

    filter_groups
        Each group contains a conjunction ('and'|'or') and a list of
        FilterDescriptors.  Within a group, conditions are combined by
        the group's conjunction.  Groups themselves are always ANDed
        together so compound filter expressions of the form
        ``(A AND B) AND (C OR D)`` are straightforward to express.

    Filtering uses correlated EXISTS subqueries — one per filter — so there
    is no join-fanout between multiple filter conditions.  Sorting uses
    LEFT JOINs on PropertyValue, one per unique sort schema. Text sorts in
    natural order (``NATURAL_SORT_COLLATION``); numbers and dates sort by
    value.

    The caller is responsible for loading PropertyValues separately via
    list_values_for_pages().
    """
    base_conds = [
        Block.parent_id == database_id,
        Block.state == 'active',
        Block.type != 'entry_template',
    ]

    # Build one clause per group; empty groups (all filters invalid) are skipped
    group_clauses = []
    for group in filter_groups:
        individual = [
            clause
            for f in group.filters
            if (clause := _build_filter_clause(f)) is not None
        ]
        if not individual:
            continue
        if group.conjunction == 'or':
            group_clauses.append(or_(*individual))
        else:
            group_clauses.append(and_(*individual))

    all_conds = and_(*base_conds, *group_clauses)

    # Total count (no pagination, no sort JOINs needed)
    total: int = db.scalar(
        select(func.count()).select_from(Block).where(all_conds)
    ) or 0

    # Data query
    stmt = select(Block).where(all_conds)

    # Build ORDER BY and accumulate any LEFT JOINs required for sort columns
    order_exprs = []
    pv_join_aliases: dict[str, aliased] = {}   # schema_id_str -> alias

    for s in sorts:
        if s.schema_id == '__name__':
            title_expr = _natural(Block.content['title'].as_string())
            order_exprs.append(
                title_expr.asc().nullslast()
                if s.direction == 'asc'
                else title_expr.desc().nullslast()
            )
            continue

        try:
            uuid.UUID(s.schema_id)
        except ValueError:
            continue

        schema_type = s.schema_type or 'text'
        alias_key = s.schema_id

        if alias_key not in pv_join_aliases:
            pv_alias = aliased(
                PropertyValue,
                name=f'pv_sort_{len(pv_join_aliases)}',
            )
            pv_join_aliases[alias_key] = pv_alias

        PV = pv_join_aliases[alias_key]

        if schema_type in ('number', 'id'):
            num_key = 'number' if schema_type == 'number' else 'id_value'
            val_expr = PV.value[num_key].as_float()
        elif schema_type in ('date', 'created_time', 'last_edited_time'):
            # Dates are stored as ISO-8601 strings.  Regular date fields use
            # the 'start' key (YYYY-MM-DD or YYYY-MM-DDTHH:MM for timed entries);
            # system timestamps use 'datetime' (full UTC ISO string).
            # ISO strings sort lexicographically in chronological order, so plain
            # string comparison is correct.  We mirror the same key mapping used
            # by the date filter branch so that sort and filter behave consistently.
            date_key = 'start' if schema_type == 'date' else 'datetime'
            val_expr = PV.value[date_key].as_string()
        elif schema_type == 'rollup':
            # Rollup values store their aggregate under the 'result' key (see
            # app/blocks/computed.py); the generic text fallback would look up a
            # non-existent key and yield NULL for every row, leaving the column
            # effectively unsorted. Numeric aggregations are cast to float so
            # they order numerically; date and raw-value aggregations sort as
            # text, where ISO-8601 date strings still order chronologically.
            rollup_fn = (s.schema_config or {}).get('function', '')
            if rollup_fn in _NUMERIC_ROLLUP_FUNCTIONS:
                val_expr = PV.value['result'].as_float()
            else:
                val_expr = _natural(PV.value['result'].as_string())
        elif schema_type == 'select':
            val_expr = _select_sort_expression(
                PV.value['option'].as_string(), s.schema_config,
            )
        else:
            val_expr = _natural(PV.value[_value_key(schema_type)].as_string())

        order_exprs.append(
            val_expr.asc().nullslast()
            if s.direction == 'asc'
            else val_expr.desc().nullslast()
        )

    # Add LEFT JOINs for sort columns
    for schema_id_str, pv_alias in pv_join_aliases.items():
        schema_uuid = uuid.UUID(schema_id_str)
        stmt = stmt.outerjoin(
            pv_alias,
            and_(
                pv_alias.page_id == Block.id,
                pv_alias.property_schema_id == schema_uuid,
            ),
        )

    # Apply order (always append position as stable tiebreaker)
    order_exprs.append(Block.position.asc())
    stmt = stmt.order_by(*order_exprs)

    stmt = stmt.limit(limit).offset(offset)

    return list(db.scalars(stmt).all()), total


# ─── Block ────────────────────────────────────────────────────────────────────

# Order of siblings under one parent. Position decides; creation time and id
# only break ties between equal positions, so every read lists such siblings
# the same way. Used wherever children are listed, see list_children.
SIBLING_ORDER = (Block.position, Block.created_at, Block.id)


def get_block(db: Session, block_id: uuid.UUID) -> Optional[Block]:
    """Return the Block with *block_id*, or ``None`` if not found."""
    return db.get(Block, block_id)


def get_block_or_raise(db: Session, block_id: uuid.UUID) -> Block:
    """
    Return the Block with *block_id*.

    Raises
    ------
    KeyError
        If no block with that ID exists.
    """
    block = db.get(Block, block_id)
    if block is None:
        raise KeyError(block_id)
    return block



def list_trash(db: Session) -> list[Block]:
    """
    Return all top-level trashed blocks, ordered by most recently updated first.

    A block is "top-level trash" when it is in state='trash' and its parent is
    either absent or in state='active'.  This gives the root item of each
    deleted subtree without surfacing every individual descendant, making the
    recycle-bin list manageable even when large page trees are deleted.
    """
    parent = aliased(Block)
    stmt = (
        select(Block)
        .outerjoin(parent, Block.parent_id == parent.id)
        .where(Block.state == 'trash')
        .where(
            or_(
                Block.parent_id.is_(None),
                parent.state == 'active',
            )
        )
        .order_by(Block.updated_at.desc())
    )
    return list(db.scalars(stmt).all())


def list_children(
    db: Session,
    parent_id: uuid.UUID,
    *,
    state: Optional[str] = "active",
    exclude_types: Optional[frozenset[str]] = None,
) -> list[Block]:
    """
    Return the direct children of *parent_id* in their order: by position,
    then by creation time and id. The last two only decide between siblings
    at equal positions, which PostgreSQL would otherwise return in no fixed
    order, so that the order of such siblings does not change between reads.

    Parameters
    ----------
    db:
        Active database session.
    parent_id:
        UUID of the parent block.
    state:
        Filter by block state. Pass ``None`` to return children of all states.
    exclude_types:
        Optional set of block type strings to exclude from the result.
        Pass ``frozenset({"entry_template"})`` when listing database entries
        so that template blocks are not surfaced as regular entries.
    """
    stmt = (
        select(Block)
        .where(Block.parent_id == parent_id)
        .order_by(*SIBLING_ORDER)
    )
    if state is not None:
        stmt = stmt.where(Block.state == state)
    if exclude_types:
        stmt = stmt.where(Block.type.not_in(exclude_types))
    return list(db.scalars(stmt).all())


def list_blocks_by_ids(
    db: Session,
    block_ids: list[uuid.UUID],
    *,
    parent_id: Optional[uuid.UUID] = None,
    state: Optional[str] = "active",
    exclude_types: Optional[frozenset[str]] = None,
) -> list[Block]:
    """
    Return the blocks whose IDs are in *block_ids*, in unspecified order.

    Every requested block is loaded in a single query (no N+1); the optional
    ``parent_id``, ``state`` and ``exclude_types`` filters are applied in SQL.
    IDs that match no row (or are filtered out) are silently omitted from the
    result.

    This resolves a known set of entry IDs independently of any paginated
    listing, which is what lets relation chips render even when the linked
    entry sits past the target database's display limit.

    Parameters
    ----------
    db:
        Active database session.
    block_ids:
        UUIDs of the blocks to load. An empty list yields an empty result
        without issuing a query.
    parent_id:
        When given, restrict the result to direct children of this block.
    state:
        Filter by block state. Pass ``None`` to return blocks of all states.
    exclude_types:
        Optional set of block type strings to exclude from the result.
    """
    if not block_ids:
        return []
    stmt = select(Block).where(Block.id.in_(block_ids))
    if parent_id is not None:
        stmt = stmt.where(Block.parent_id == parent_id)
    if state is not None:
        stmt = stmt.where(Block.state == state)
    if exclude_types:
        stmt = stmt.where(Block.type.not_in(exclude_types))
    return list(db.scalars(stmt).all())


def list_databases(db: Session) -> list[Block]:
    """
    Return all active blocks of type ``'database'``, ordered by position.

    Used to populate relation-property target-database pickers.

    Parameters
    ----------
    db:
        Active database session.
    """
    stmt = (
        select(Block)
        .where(Block.type == "database", Block.state == "active")
        .order_by(Block.position)
    )
    return list(db.scalars(stmt).all())


def create_block(
    db: Session,
    *,
    type: str,
    position: float,
    parent_id: Optional[uuid.UUID] = None,
    reference_id: Optional[uuid.UUID] = None,
    content: Optional[dict] = None,
    state: str = "active",
    icon: Optional[str] = None,
    cover: Optional[str] = None,
) -> Block:
    """
    Insert a new Block and return the flushed (but not committed) instance.

    Parameters
    ----------
    db:
        Active database session.
    type:
        Block type string, e.g. ``'page'``, ``'paragraph'``, ``'database'``.
    position:
        Fractional index position among siblings.
    parent_id:
        UUID of the parent block. ``None`` for workspace-level blocks.
    reference_id:
        UUID of the source block for reference blocks such as
        ``'database_view'``. ``None`` for all other types.
    content:
        Type-specific payload stored as JSONB. ``None`` when not applicable.
    state:
        Initial state. Defaults to ``'active'``.
    icon:
        Iconify icon string, e.g. ``"mdi:file-document"``.
    cover:
        Cover value: image URL or ``"gradient:..."`` string.
    """
    block = Block(
        type=type,
        position=position,
        parent_id=parent_id,
        reference_id=reference_id,
        content=content,
        state=state,
        icon=icon,
        cover=cover,
    )
    db.add(block)
    db.flush()
    db.refresh(block)
    return block


def update_block(
    db: Session,
    block: Block,
    *,
    type: Optional[str] = None,
    content: Optional[dict] = None,
    position: Optional[float] = None,
    parent_id: Optional[uuid.UUID] = None,
    state: Optional[str] = None,
    icon: Optional[str] = None,
    cover: Optional[str] = None,
) -> Block:
    """
    Apply the supplied keyword arguments to *block* and flush the change.

    Only fields passed as explicit keyword arguments are modified; ``None``
    as a default means "do not touch this field". To explicitly set a field
    to ``None``, the service layer must assign it directly.
    """
    if type is not None:
        block.type = type
    if content is not None:
        block.content = content
    if position is not None:
        block.position = position
    if parent_id is not None:
        block.parent_id = parent_id
    if state is not None:
        block.state = state
    if icon is not None:
        block.icon = icon
    if cover is not None:
        block.cover = cover
    db.flush()
    db.refresh(block)
    return block


# ─── PropertySchema ───────────────────────────────────────────────────────────


def get_schema(db: Session, schema_id: uuid.UUID) -> Optional[PropertySchema]:
    """Return the PropertySchema with *schema_id*, or ``None`` if not found."""
    return db.get(PropertySchema, schema_id)


def get_schema_by_name(
    db: Session, database_id: uuid.UUID, name: str
) -> Optional[PropertySchema]:
    """
    Return the PropertySchema with the given *name* in *database_id*, or
    ``None`` if not found.
    """
    stmt = select(PropertySchema).where(
        PropertySchema.database_id == database_id,
        PropertySchema.name == name,
    )
    return db.scalars(stmt).first()


def list_schemas(db: Session, database_id: uuid.UUID) -> list[PropertySchema]:
    """Return all PropertySchemas for *database_id*, ordered by ``position``."""
    stmt = (
        select(PropertySchema)
        .where(PropertySchema.database_id == database_id)
        .order_by(PropertySchema.position)
    )
    return list(db.scalars(stmt).all())


def list_schemas_of_active_databases(
    db: Session,
) -> dict[uuid.UUID, list[PropertySchema]]:
    """
    Return the PropertySchemas of every active database in one query, grouped
    by database.

    The databases appear in the order of ``list_databases`` (by position) and
    each database's schemas in the order of ``list_schemas``. A database
    without any schema is absent from the result.
    """
    stmt = (
        select(PropertySchema)
        .join(Block, Block.id == PropertySchema.database_id)
        .where(Block.type == "database", Block.state == "active")
        .order_by(Block.position, Block.id, PropertySchema.position)
    )
    result: dict[uuid.UUID, list[PropertySchema]] = {}
    for schema in db.scalars(stmt).all():
        result.setdefault(schema.database_id, []).append(schema)
    return result


def list_relation_schemas_by_key_property(
    db: Session,
    key_property_id: uuid.UUID,
) -> list[PropertySchema]:
    """
    Return every relation PropertySchema whose keying config points at
    *key_property_id*, ordered by database and position.

    Keying stores a read-side pointer into the target database at
    ``config.keying.key_property_id``.  Because it lives inside a JSON blob the
    reference cannot be expressed as a foreign key, so deleting or retyping the
    referenced property has to be resolved by scanning for referrers.

    The scan is derived state on purpose.  A boolean marker on the referenced
    property would have to be cleared on every path that disables keying, and
    would silently desynchronise the moment one of those paths is missed; it
    also could not name the affected relations, only assert that some exist.

    Only *enabled* keying blocks count as a reference.  A relation that was
    reset to vanilla but kept its pointer — the settings modal preserves the
    selection so re-enabling is one click — is dormant: nothing reads it, and
    deleting the pointed-at property changes nothing the user can observe.
    Reporting it would make the delete confirmation name relations it does not
    actually alter.

    Filtering happens in Python rather than through a JSON path predicate: the
    candidate set is just the relation schemas of the workspace, small enough
    that a readable Python filter costs nothing measurable.
    """
    target = str(key_property_id)
    stmt = (
        select(PropertySchema)
        .where(PropertySchema.type == "relation")
        .order_by(PropertySchema.database_id, PropertySchema.position)
    )
    result: list[PropertySchema] = []
    for schema in db.scalars(stmt).all():
        keying = (schema.config or {}).get("keying")
        if not isinstance(keying, dict):
            continue
        if keying.get("enabled") is not True:
            continue
        if str(keying.get("key_property_id") or "") == target:
            result.append(schema)
    return result


def create_schema(
    db: Session,
    *,
    database_id: uuid.UUID,
    name: str,
    type: str,
    position: float,
    config: Optional[dict] = None,
    visible_in_view_id: Optional[uuid.UUID] = None,
) -> PropertySchema:
    """
    Insert a new PropertySchema for *database_id* and return it.

    A new property starts hidden in every view of the database, so it does not
    surface in views that were arranged without it. *visible_in_view_id* names
    the one view, if any, where it should appear right away: the view it was
    added from.
    """
    from app.blocks import views  # local import — views imports this module

    schema = PropertySchema(
        database_id=database_id,
        name=name,
        type=type,
        position=position,
        config=config,
    )
    db.add(schema)
    db.flush()
    db.refresh(schema)
    views.hide_schema_in_views(db, database_id, schema.id, except_view_id=visible_in_view_id)
    return schema


def update_schema(
    db: Session,
    schema: PropertySchema,
    *,
    name: Optional[str] = None,
    type: Optional[str] = None,
    config: Optional[dict] = None,
    position: Optional[float] = None,
) -> PropertySchema:
    """Apply the supplied keyword arguments to *schema* and flush the change."""
    if name is not None:
        schema.name = name
    if type is not None:
        schema.type = type
    if config is not None:
        schema.config = config
    if position is not None:
        schema.position = position
    db.flush()
    db.refresh(schema)
    return schema


def delete_schema(db: Session, schema: PropertySchema) -> None:
    """
    Delete *schema* and cascade-delete all associated PropertyValues.

    Every view of the database forgets the property in the same transaction:
    filters and sorts on it, its column order, width, visibility and
    aggregation entries, and any grouping or calendar setting pointing at it.
    """
    from app.blocks import views  # local import — views imports this module

    views.forget_schema_in_views(db, schema.database_id, schema.id)
    db.delete(schema)
    db.flush()


def infer_formula_result_types(
    db: Session, schema_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """
    Return the result type each formula property currently produces.

    A formula's result type is not part of its configuration; it is recorded
    with every computed value under ``result_type``. The first stored value
    that has a result and a result type decides, which is what filters on
    formula columns need to compare correctly. Properties without such a value are absent from the
    result, and callers fall back to text.
    """
    if not schema_ids:
        return {}
    result_type = PropertyValue.value['result_type'].as_string()
    stmt = (
        select(PropertyValue.property_schema_id, result_type)
        .where(
            PropertyValue.property_schema_id.in_(schema_ids),
            result_type.is_not(None),
            PropertyValue.value['result'].as_string().is_not(None),
        )
        .ext(distinct_on(PropertyValue.property_schema_id))
        .order_by(PropertyValue.property_schema_id)
    )
    return {schema_id: value for schema_id, value in db.execute(stmt)}


def ensure_readonly_schemas(db: Session, database_id: uuid.UUID) -> list[PropertySchema]:
    """
    Create the system-managed property schemas a database is missing.

    Idempotent: schemas that already exist are left unchanged, and only the
    newly created ones are returned. Flushes but does not commit.

    Schemas seeded (in order)
    -------------------------
    id               – auto-incrementing sequential integer
    created_by       – username of the entry creator
    created_time     – ISO-8601 UTC creation timestamp
    last_edited_by   – username of the last editor
    last_edited_time – ISO-8601 UTC last-edit timestamp
    parent_item      – single-parent relation (user-writable)
    sub_item         – backend-managed child list (mirror of parent_item)

    The ``parent_item`` / ``sub_item`` pair is always created together.
    Each schema's ``config.partner_schema_id`` stores the UUID of its
    counterpart so the sync helper can resolve the mirror without a
    full schema scan.
    """
    existing = list_schemas(db, database_id)
    existing_types = {s.type for s in existing}

    readonly_defaults: list[dict] = [
        {"type": "id",               "name": "ID",                "config": {"prefix": "", "next_id": 1}},
        {"type": "created_by",       "name": "Created by",        "config": None},
        {"type": "created_time",     "name": "Created time",      "config": None},
        {"type": "last_edited_by",   "name": "Last edited by",    "config": None},
        {"type": "last_edited_time", "name": "Last edited time",  "config": None},
    ]

    created: list = []
    position = (existing[-1].position + 1.0) if existing else 1.0

    for defn in readonly_defaults:
        if defn["type"] not in existing_types:
            schema = create_schema(
                db,
                database_id=database_id,
                name=defn["name"],
                type=defn["type"],
                position=position,
                config=defn["config"],
            )
            created.append(schema)
            position += 1.0

    # ── Sub-item pair ────────────────────────────────────────────────────────
    # Create parent_item and sub_item together so their partner_schema_id
    # references can be back-filled in a single commit.
    has_parent_item = "parent_item" in existing_types
    has_sub_item    = "sub_item"    in existing_types

    if not has_parent_item and not has_sub_item:
        pi_schema = create_schema(
            db,
            database_id=database_id,
            name="Parent item",
            type="parent_item",
            position=position,
            config={},
        )
        position += 1.0
        si_schema = create_schema(
            db,
            database_id=database_id,
            name="Sub-items",
            type="sub_item",
            position=position,
            config={"partner_schema_id": str(pi_schema.id)},
        )
        position += 1.0
        # Back-fill the partner reference on parent_item now that sub_item has an ID.
        update_schema(db, pi_schema, config={"partner_schema_id": str(si_schema.id)})
        created.extend([pi_schema, si_schema])

    elif not has_parent_item:
        # sub_item already exists without its partner — create parent_item and link.
        si_existing = next((s for s in existing if s.type == "sub_item"), None)
        pi_schema = create_schema(
            db,
            database_id=database_id,
            name="Parent item",
            type="parent_item",
            position=position,
            config={"partner_schema_id": str(si_existing.id)} if si_existing else {},
        )
        created.append(pi_schema)

    elif not has_sub_item:
        # parent_item already exists without its partner — create sub_item and link.
        pi_existing = next((s for s in existing if s.type == "parent_item"), None)
        si_schema = create_schema(
            db,
            database_id=database_id,
            name="Sub-items",
            type="sub_item",
            position=position,
            config={"partner_schema_id": str(pi_existing.id)} if pi_existing else {},
        )
        if pi_existing and not (pi_existing.config or {}).get("partner_schema_id"):
            update_schema(
                db, pi_existing,
                config={"partner_schema_id": str(si_schema.id)},
            )
        created.append(si_schema)

    return created


# ─── PropertyValue ────────────────────────────────────────────────────────────


def get_value(
    db: Session, page_id: uuid.UUID, schema_id: uuid.UUID
) -> Optional[PropertyValue]:
    """Return the PropertyValue for *page_id* / *schema_id*, or ``None``."""
    stmt = select(PropertyValue).where(
        PropertyValue.page_id == page_id,
        PropertyValue.property_schema_id == schema_id,
    )
    return db.scalars(stmt).first()


def list_values(db: Session, page_id: uuid.UUID) -> list[PropertyValue]:
    """Return all PropertyValues for *page_id*."""
    stmt = select(PropertyValue).where(PropertyValue.page_id == page_id)
    return list(db.scalars(stmt).all())


def list_values_for_pages(
    db: Session,
    page_ids: list[uuid.UUID],
) -> dict[uuid.UUID, list[PropertyValue]]:
    """
    Return all PropertyValues for a list of entry blocks in a single query,
    grouped by page_id.

    This avoids N+1 queries when loading all entries of a database block.
    """
    result: dict[uuid.UUID, list[PropertyValue]] = {pid: [] for pid in page_ids}
    if not page_ids:
        return result
    stmt = select(PropertyValue).where(PropertyValue.page_id.in_(page_ids))
    for pv in db.scalars(stmt).all():
        result[pv.page_id].append(pv)
    return result


def list_relation_values_mentioning(
    db: Session,
    database_id: uuid.UUID,
    schema_ids: set[uuid.UUID],
    entry_id: str,
) -> list[tuple[uuid.UUID, uuid.UUID, Optional[dict]]]:
    """
    Return ``(page_id, schema_id, value)`` for the values of *schema_ids* held
    by active entries of *database_id* whose JSON text contains *entry_id* in
    quotes, ordered by entry position.

    One query regardless of the size of the database. The match is on the
    serialised value, so it finds an id in a plain ``related_ids`` list as
    well as in any slot of a ``_timeline``; it is a superset of the exact
    answer, and callers check each returned value against the shape they
    expect. An empty *schema_ids* returns nothing without a query.
    """
    if not schema_ids:
        return []
    stmt = (
        select(
            PropertyValue.page_id,
            PropertyValue.property_schema_id,
            PropertyValue.value,
        )
        .join(Block, Block.id == PropertyValue.page_id)
        .where(
            Block.parent_id == database_id,
            Block.state == "active",
            PropertyValue.property_schema_id.in_(schema_ids),
            _like_contains(cast(PropertyValue.value, String), f'"{entry_id}"'),
        )
        .order_by(Block.position, PropertyValue.page_id, PropertyValue.property_schema_id)
    )
    return [
        (page_id, schema_id, value)
        for page_id, schema_id, value in db.execute(stmt)
    ]


def upsert_value(
    db: Session,
    *,
    page_id: uuid.UUID,
    schema_id: uuid.UUID,
    value: Optional[dict],
) -> PropertyValue:
    """
    Insert or update the PropertyValue for *page_id* / *schema_id*.

    If a record already exists for this combination, its ``value`` field is
    updated in place. Otherwise a new record is created.
    """
    pv = get_value(db, page_id, schema_id)
    if pv is None:
        pv = PropertyValue(
            page_id=page_id,
            property_schema_id=schema_id,
            value=value,
        )
        db.add(pv)
    else:
        pv.value = value
    db.flush()
    db.refresh(pv)
    return pv


# ─── BlockPreference ──────────────────────────────────────────────────────────


def get_preference(
    db: Session, block_id: uuid.UUID, key: str
) -> Optional[BlockPreference]:
    """Return the BlockPreference for *block_id* / *key*, or ``None``."""
    stmt = select(BlockPreference).where(
        BlockPreference.block_id == block_id,
        BlockPreference.key == key,
    )
    return db.scalars(stmt).first()


def upsert_preference(
    db: Session,
    block_id: uuid.UUID,
    key: str,
    value: Optional[object],
) -> BlockPreference:
    """Insert or update a BlockPreference for *block_id* / *key*."""
    pref = get_preference(db, block_id, key)
    if pref is None:
        pref = BlockPreference(block_id=block_id, key=key, value=value)
        db.add(pref)
    else:
        pref.value = value
    db.flush()
    db.refresh(pref)
    return pref


def list_preferences(db: Session, block_id: uuid.UUID) -> list[BlockPreference]:
    """Return all BlockPreferences for *block_id*."""
    stmt = select(BlockPreference).where(BlockPreference.block_id == block_id)
    return list(db.scalars(stmt).all())


# ─── BlockEvent ───────────────────────────────────────────────────────────────


def list_events(
    db: Session,
    block_id: uuid.UUID,
    *,
    limit: int = 100,
) -> list[BlockEvent]:
    """Return the most recent *limit* events for *block_id*, newest first."""
    stmt = (
        select(BlockEvent)
        .where(BlockEvent.block_id == block_id)
        .order_by(BlockEvent.created_at.desc())
        .limit(limit)
    )
    return list(db.scalars(stmt).all())


def get_event(db: Session, event_id: uuid.UUID) -> Optional[BlockEvent]:
    """Return a single BlockEvent by its primary key, or ``None``."""
    return db.get(BlockEvent, event_id)
