"""
Computed property engine
========================

Builds the dependency graph between formula / rollup schemas and evaluates
computed values for a single database entry.

Public API
----------
  build_dependency_graph(schemas)           -> graph
  has_any_cycle(graph)                      -> bool
  topological_order(graph)                  -> list[uuid.UUID]
  compute_all_for_entry(db, db_id, entry_id) -> None

Cycle detection
---------------
Kahn's topological-sort algorithm is used.  It detects all cycles in O(V+E),
not just the first one.

Dependency graph semantics
--------------------------
``graph[schema_id] = {dep_id, …}`` means schema_id depends on each dep_id.
The evaluation order is the reverse topological order (dependencies first).

For formula schemas the dependencies are extracted from the expression via
``formula_engine.extract_prop_names``, then resolved to schema UUIDs by name.

For rollup schemas the dependency is the relation schema pointed to by
``config.relation_schema_id``.

Schema-like protocol
--------------------
The graph builder operates on any object with ``.id``, ``.name``, ``.type``,
and ``.config`` attributes.  This allows passing temporary sentinel objects
during cycle-check validation without touching the database.
"""

from __future__ import annotations

import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from app.blocks import repository as repo
from app.blocks.formula_engine import FormulaError, evaluate, extract_prop_names


class CycleError(Exception):
    """Raised when a circular dependency is detected among computed schemas."""


# ─── Lightweight schema proxy for pre-flight checks ──────────────────────────


@dataclass
class SchemaLike:
    """Minimal schema representation used for cycle-detection dry-runs."""

    id: uuid.UUID
    name: str
    type: str
    config: Optional[dict]


# ─── Dependency graph ─────────────────────────────────────────────────────────


def build_dependency_graph(schemas: list[Any]) -> dict[uuid.UUID, set[uuid.UUID]]:
    """
    Build a ``schema_id → {dep_schema_id, …}`` dependency map.

    Only formula and rollup schemas appear as *keys*. Any schema type may
    appear as a *value* (dependency target).

    Parameters
    ----------
    schemas:
        All PropertySchema (or SchemaLike) objects for a database.
    """
    name_to_id: dict[str, uuid.UUID] = {s.name: s.id for s in schemas}
    graph: dict[uuid.UUID, set[uuid.UUID]] = {}

    for schema in schemas:
        if schema.type == "formula":
            expr = (schema.config or {}).get("expression", "")
            if not expr:
                graph[schema.id] = set()
                continue
            try:
                names = extract_prop_names(expr)
            except FormulaError:
                graph[schema.id] = set()
                continue
            deps: set[uuid.UUID] = set()
            for name in names:
                dep_id = name_to_id.get(name)
                if dep_id is not None and dep_id != schema.id:
                    deps.add(dep_id)
            graph[schema.id] = deps

        elif schema.type == "rollup":
            config = schema.config or {}
            deps = set()
            for key in ("relation_schema_id",):
                raw = config.get(key)
                if raw:
                    try:
                        deps.add(uuid.UUID(str(raw)))
                    except ValueError:
                        pass
            graph[schema.id] = deps

    return graph


# ─── Cycle detection (Kahn's algorithm) ──────────────────────────────────────


def has_any_cycle(graph: dict[uuid.UUID, set[uuid.UUID]]) -> bool:
    """
    Return ``True`` if *graph* contains at least one cycle.

    Uses Kahn's algorithm (topological sort).  Time complexity O(V + E).

    The graph maps ``node → {predecessors}``.  An edge ``A → B`` means
    "A depends on B" (B must be evaluated before A).
    """
    all_nodes: set[uuid.UUID] = set(graph.keys())
    for deps in graph.values():
        all_nodes |= deps

    # in_degree[n] = number of predecessors not yet processed
    in_degree: dict[uuid.UUID, int] = {n: 0 for n in all_nodes}
    # successors[dep] = [nodes that depend on dep]
    successors: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)

    for src, deps in graph.items():
        in_degree[src] = len(deps)
        for dep in deps:
            successors[dep].append(src)

    queue: deque[uuid.UUID] = deque(n for n in all_nodes if in_degree[n] == 0)
    processed = 0

    while queue:
        node = queue.popleft()
        processed += 1
        for s in successors.get(node, []):
            in_degree[s] -= 1
            if in_degree[s] == 0:
                queue.append(s)

    return processed < len(all_nodes)


# ─── Topological evaluation order ────────────────────────────────────────────


def topological_order(graph: dict[uuid.UUID, set[uuid.UUID]]) -> list[uuid.UUID]:
    """
    Return computed schema IDs in evaluation order (dependencies first).

    Only IDs that appear as *keys* in *graph* (formula / rollup schemas) are
    returned; non-computed dependency nodes are omitted from the result.

    Raises ``CycleError`` if a cycle is detected.
    """
    all_nodes: set[uuid.UUID] = set(graph.keys())
    for deps in graph.values():
        all_nodes |= deps

    in_degree: dict[uuid.UUID, int] = {n: 0 for n in all_nodes}
    successors: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)

    for src, deps in graph.items():
        in_degree[src] = len(deps)
        for dep in deps:
            successors[dep].append(src)

    queue: deque[uuid.UUID] = deque(n for n in all_nodes if in_degree[n] == 0)
    order: list[uuid.UUID] = []

    while queue:
        node = queue.popleft()
        order.append(node)
        for s in successors.get(node, []):
            in_degree[s] -= 1
            if in_degree[s] == 0:
                queue.append(s)

    if len(order) < len(all_nodes):
        raise CycleError("Circular dependency detected in computed schemas")

    # Return only the keys (computed schemas) in topological order
    key_set = set(graph.keys())
    return [sid for sid in order if sid in key_set]


# ─── Timeline helpers ─────────────────────────────────────────────────────────


def _last_timeline_slot(timeline: dict) -> Optional[dict]:
    """
    Return the value of the chronologically last slot in a ``_timeline`` dict.

    The special empty-string key ``""`` (always-valid singleton) is returned
    directly when present.  All other keys are sorted lexicographically by
    their start timestamp — ISO 8601 strings sort correctly this way.

    Returns ``None`` when *timeline* is empty.
    """
    if not timeline:
        return None
    if "" in timeline:
        return timeline[""]  # type: ignore[return-value]

    def _slot_start_key(k: str) -> str:
        # "→end" keys have no start — they precede everything.
        if k.startswith("→"):
            return ""
        # "start→end" or "start→": take the start part.
        return k.split("→")[0]

    last_key = max(timeline.keys(), key=_slot_start_key)
    return timeline[last_key]  # type: ignore[return-value]


def _get_related_ids(rel_value: Optional[dict], rel_schema: Any) -> list[str]:
    """
    Extract the current ``related_ids`` from a relation PropertyValue.

    Timeline-aware: when ``config.hasTimeline`` is true, reads from the last
    ``_timeline`` slot.  For non-timeline relations falls back to the plain
    ``related_ids`` list.

    Always returns a plain Python list of strings (possibly empty).
    """
    if rel_value is None:
        return []
    has_timeline = (rel_schema.config or {}).get("hasTimeline", False) if rel_schema else False
    if has_timeline and "_timeline" in rel_value:
        slot = _last_timeline_slot(rel_value.get("_timeline") or {})
        if slot is None:
            return []
        return list(slot.get("related_ids", []))
    return list(rel_value.get("related_ids", []))


def _entry_id_in_relation_value(entry_id_str: str, rel_value: Optional[dict]) -> bool:
    """
    Return True if *entry_id_str* appears in any ``related_ids`` list inside
    *rel_value*, whether the value uses a plain list or a ``_timeline`` dict.

    Used by cascade helpers to decide whether a sibling entry depends on
    the changed entry without needing to know the relation schema's config.
    """
    if not rel_value:
        return False
    if "_timeline" in rel_value:
        for slot_val in (rel_value.get("_timeline") or {}).values():
            if isinstance(slot_val, dict):
                if entry_id_str in [str(r) for r in slot_val.get("related_ids", [])]:
                    return True
        return False
    return entry_id_str in [str(r) for r in rel_value.get("related_ids", [])]


# ─── Scalar extraction from JSONB values ─────────────────────────────────────


def _extract_scalar(schema_type: str, value: Optional[dict], config: Optional[dict] = None) -> Any:
    """
    Extract a single scalar from a PropertyValue JSONB dict.

    The optional *config* parameter is used for timeline-aware relation
    extraction: when ``config.hasTimeline`` is true, the related IDs are taken
    from the last ``_timeline`` slot rather than the root ``related_ids``.

    For value-bearing scalar columns (text, email, phone, url, number,
    checkbox, select, date) the extraction is also timeline-aware: when the
    stored value carries a ``_timeline`` key, the last slot's payload is used,
    matching the documented "default to last state" behaviour for timelined
    properties.
    """
    if value is None:
        return None

    # Timeline-aware scalar extraction (#4). Timelined text / email / phone /
    # url / number / checkbox / select / date columns store their per-period
    # payload inside ``_timeline`` slots rather than at the root. Mirroring the
    # documented "default to last state" behaviour for timelined properties
    # (see README), resolve the last slot and extract the scalar from it.
    # Relation-style columns keep their own timeline-aware branch below;
    # formula / rollup values never carry a ``_timeline`` key.
    if schema_type in (
        "text", "email", "phone", "url",
        "number", "checkbox", "select", "date",
    ) and "_timeline" in value:
        slot = _last_timeline_slot(value.get("_timeline") or {})
        value = slot if slot is not None else {}

    if schema_type in ("text", "email", "phone", "url"):
        # Flat text stores its payload under "text"; timelined email / phone /
        # url slots store it under "value" (see TimelineEditor.buildEmptyValue).
        # Prefer the explicit "text" key — preserving empty strings — and fall
        # back to "value" so both storage shapes resolve correctly.
        if "text" in value:
            return value.get("text")
        return value.get("value")
    if schema_type == "number":
        return value.get("number")
    if schema_type == "checkbox":
        return value.get("checked")
    if schema_type == "select":
        # Single-select (frontend): {"option": "label"}.
        # Multi-select (frontend):  {"options": ["label", …]}.
        # Legacy paths: {"selected": "label"} or {"selected": ["a", "b"]}.
        #
        # For multi-valued shapes the first label is returned so that scalar
        # comparisons such as prop('Typ') == "Geburt" work for the common
        # single-value case; format()/contains() can be used over the full set
        # via the relation-style list handling elsewhere.
        if "option" in value:
            raw = value["option"]
        elif "options" in value:
            raw = value.get("options")
        else:
            raw = value.get("selected")
        if isinstance(raw, list):
            return raw[0] if raw else None
        return raw
    if schema_type == "date":
        # Return the full dict so formulas can access both start and end via
        # dateStart() / dateEnd(). _to_datetime() in formula_engine handles
        # the {"start": …, "end": …} shape transparently.
        return {"start": value.get("start"), "end": value.get("end")}
    if schema_type == "id":
        return value.get("id_value")
    if schema_type in ("relation", "parent_item", "sub_item"):
        # Return the list of related entry IDs so empty() can check whether
        # any entries are actually linked.  ``relation`` may be timeline-aware
        # (if hasTimeline is set the current last-slot list is returned);
        # ``parent_item`` / ``sub_item`` are the hierarchy mirrors and store a
        # flat ``related_ids`` list (config locked, never timelined).
        has_timeline = (config or {}).get("hasTimeline", False)
        if has_timeline and "_timeline" in value:
            slot = _last_timeline_slot(value.get("_timeline") or {})
            return list(slot.get("related_ids", [])) if slot else []
        return value.get("related_ids", [])
    if schema_type in ("formula", "rollup"):
        # A formula or rollup whose result is a relation-descriptor list
        # (marked with relation=True, e.g. a formula returning prop('Ort') or
        # a relation rollup) is treated like a relation column when it is
        # itself rolled up: return the list of related entry IDs so the
        # relation-aware rollup path can resolve them to chips. Empty results
        # therefore yield an empty list rather than a placeholder. All other
        # results pass through unchanged.
        result = value.get("result")
        if value.get("relation") is True and isinstance(result, list):
            ids: list[str] = []
            for item in result:
                if isinstance(item, dict) and "id" in item:
                    ids.append(str(item["id"]))
            return ids
        return result
    if schema_type in ("created_by", "last_edited_by"):
        return value.get("user_id") or value.get("username")  # user_id (new) or username (legacy)
    if schema_type in ("created_time", "last_edited_time"):
        return value.get("datetime")
    return None


# ─── Rollup aggregation ───────────────────────────────────────────────────────

_ROLLUP_FUNCTIONS: frozenset[str] = frozenset({
    # ── Count family ──────────────────────────────────────────────────────────
    "count",            # total linked entries (including null)
    "count_values",     # entries where property is non-null
    "count_empty",      # entries where property is null
    "count_not_empty",  # entries where property is non-null (alias for count_values)
    "count_unique",     # distinct non-null values
    # ── Percent family ────────────────────────────────────────────────────────
    "percent_empty",        # % of null entries
    "percent_not_empty",    # % of non-null entries
    "percent_checked",      # % of True values (checkbox columns)
    "percent_unchecked",    # % of False values (checkbox columns)
    "percent_per_option",   # {option_label: %} breakdown for select columns
    # ── Checkbox aggregation ──────────────────────────────────────────────────
    "checked",              # count of True values (checkbox columns)
    # ── Numeric aggregations ──────────────────────────────────────────────────
    "sum",
    "avg",
    "median",
    "min",
    "max",
    "range",    # max − min
    # ── Raw value retrieval ───────────────────────────────────────────────────
    "show_original",  # result: list of all raw scalars
    "first_value",    # first non-null scalar (useful for 1-to-1 relations)
    "last_value",     # last non-null scalar
    # ── Date aggregations ─────────────────────────────────────────────────────
    "earliest_date",  # earliest datetime across related entries
    "latest_date",    # latest datetime across related entries
    "date_range",     # ISO string "start → end" spanning earliest to latest
})

# Rollup target column types that store related entry IDs. When such a column
# is rolled up with a raw-display function, the IDs are resolved to entry
# titles for human-readable output (#11).
_RELATION_COL_TYPES = frozenset({"relation", "parent_item", "sub_item"})
_RAW_DISPLAY_FUNCTIONS = frozenset({"show_original", "first_value", "last_value"})


def _resolve_relation_entries(
    scalars: list[Any],
    resolve_entry: Callable[[str], Optional[dict]],
) -> list[dict]:
    """
    Flatten relation-rollup *scalars* (each a list of related entry IDs) into a
    flat list of entry descriptors for clickable chips.

    ``resolve_entry`` maps an entry ID string to a descriptor dict
    ``{"id", "title", "database_id"}`` for an active entry, or ``None`` for a
    missing/trashed entry (which is skipped).
    """
    out: list[dict] = []
    for scalar in scalars:
        if not scalar:
            continue
        for rid_raw in scalar:
            entry = resolve_entry(str(rid_raw))
            if entry is not None:
                out.append(entry)
    return out


def _resolve_formula_relation(
    result: Any,
    resolve_entry: Callable[[str], Optional[dict]],
) -> tuple[Any, bool]:
    """
    Detect and normalise a formula result that represents relation targets.

    Returns ``(resolved_result, True)`` when *result* is:

    * A ``list[str]`` whose every item is a valid UUID string — each UUID is
      passed through *resolve_entry*; missing / inactive entries are skipped.
    * A ``list[dict]`` where every item already has ``id`` and ``title`` keys
      (produced by a ``show_original`` rollup over a relation column whose
      value was passed through ``prop()`` in the formula).
    * A single ``dict`` with ``id`` and ``title`` keys (produced by a
      ``first_value`` / ``last_value`` rollup over a relation column); wrapped
      in a list for uniform downstream handling.

    Returns ``(result, False)`` unchanged for all other shapes so the normal
    scalar serialisation path is used.
    """
    # Single entry descriptor (first_value / last_value rollup over relation)
    if isinstance(result, dict) and "id" in result and "title" in result:
        return [result], True

    if not isinstance(result, list) or not result:
        return result, False

    first = result[0]

    # Already-resolved list (show_original rollup over relation column)
    if isinstance(first, dict) and "id" in first and "title" in first:
        return result, True

    # Raw strings from a relation property; bail out if any are not UUIDs
    # so that plain string lists are never mistaken for relation results.
    if not isinstance(first, str):
        return result, False

    for item in result:
        if not isinstance(item, str):
            return result, False
        try:
            uuid.UUID(item)
        except ValueError:
            return result, False

    # All items are valid UUID strings — resolve to entry descriptors.
    entries: list[dict] = []
    for item in result:
        entry = resolve_entry(item)
        if entry is not None:
            entries.append(entry)
    return entries, True


def _aggregate(values: list[Any], function: str) -> Any:
    """
    Apply a rollup aggregation function to a list of scalar values.

    Parameters
    ----------
    values:
        One scalar per related entry, in relation order.  May contain ``None``
        for entries where the target property has no value set.
    function:
        One of the strings in ``_ROLLUP_FUNCTIONS``.

    Returns a scalar (number/string/bool), a list (show_original), or a dict
    (percent_per_option).  Returns ``None`` when the result is undefined
    (e.g. computing a numeric aggregate over an all-null dataset).
    """
    total = len(values)
    non_null = [v for v in values if v is not None]
    null_count = total - len(non_null)

    # ── Count family ──────────────────────────────────────────────────────────

    if function == "count":
        return total

    if function in ("count_values", "count_not_empty"):
        return len(non_null)

    if function == "count_empty":
        return null_count

    if function == "count_unique":
        try:
            return len(set(non_null))
        except TypeError:
            # unhashable values (e.g. dicts) — fall back to slow unique count
            unique: list[Any] = []
            for v in non_null:
                if v not in unique:
                    unique.append(v)
            return len(unique)

    # ── Percent family ────────────────────────────────────────────────────────

    if function == "percent_empty":
        if total == 0:
            return None
        return round(null_count / total * 100, 2)

    if function == "percent_not_empty":
        if total == 0:
            return None
        return round(len(non_null) / total * 100, 2)

    if function == "percent_checked":
        if total == 0:
            return None
        return round(sum(1 for v in values if v is True) / total * 100, 2)

    if function == "percent_unchecked":
        if total == 0:
            return None
        return round(sum(1 for v in values if v is False) / total * 100, 2)

    if function == "checked":
        return sum(1 for v in values if v is True)

    if function == "percent_per_option":
        if total == 0:
            return {}
        counts: dict[str, int] = {}
        for v in non_null:
            key = str(v)
            counts[key] = counts.get(key, 0) + 1
        return {k: round(c / total * 100, 2) for k, c in counts.items()}

    # ── Raw value retrieval ───────────────────────────────────────────────────

    if function == "show_original":
        return values  # full list including None; frontend renders as chips

    if function == "first_value":
        return non_null[0] if non_null else None

    if function == "last_value":
        return non_null[-1] if non_null else None

    # ── Numeric aggregations (all require at least one non-null value) ─────────

    if not non_null:
        return None

    if function == "sum":
        try:
            return sum(float(v) for v in non_null)
        except (TypeError, ValueError):
            return None

    if function == "avg":
        try:
            nums = [float(v) for v in non_null]
            return sum(nums) / len(nums)
        except (TypeError, ValueError):
            return None

    if function == "median":
        try:
            nums = sorted(float(v) for v in non_null)
            mid = len(nums) // 2
            if len(nums) % 2:
                return nums[mid]
            return (nums[mid - 1] + nums[mid]) / 2
        except (TypeError, ValueError):
            return None

    if function == "min":
        try:
            return min(non_null)
        except (TypeError, ValueError):
            return None

    if function == "max":
        try:
            return max(non_null)
        except (TypeError, ValueError):
            return None

    if function == "range":
        try:
            nums = [float(v) for v in non_null]
            return max(nums) - min(nums)
        except (TypeError, ValueError):
            return None

    # ── Date aggregations ─────────────────────────────────────────────────────
    # Values may be ISO strings, datetime objects, or date-dict {"start":...}.

    if function in ("earliest_date", "latest_date", "date_range"):
        from datetime import datetime as _DT
        import re as _re
        _ISO_RE = _re.compile(r"\d{4}-\d{2}-\d{2}")

        def _to_dt(v: Any):
            if isinstance(v, _DT):
                return v
            if isinstance(v, dict):
                v = v.get("start") or v.get("end")
            if isinstance(v, str) and _ISO_RE.match(v.strip()):
                try:
                    return _DT.fromisoformat(v.strip().replace("Z", "+00:00"))
                except ValueError:
                    pass
            return None

        dts = [_to_dt(v) for v in non_null]
        dts = [d for d in dts if d is not None]
        if not dts:
            return None

        earliest = min(dts)
        latest = max(dts)

        if function == "earliest_date":
            return earliest.isoformat()
        if function == "latest_date":
            return latest.isoformat()
        if function == "date_range":
            e_str = earliest.date().isoformat()
            l_str = latest.date().isoformat()
            return e_str if e_str == l_str else f"{e_str} → {l_str}"

    return None


# ─── Evaluation helpers ───────────────────────────────────────────────────────


def _formula_context(
    schemas: list[Any],
    values_map: dict[uuid.UUID, Optional[dict]],
) -> dict[str, Any]:
    """
    Build ``{property_name: scalar}`` dict for formula evaluation.

    Passes each schema's ``config`` to ``_extract_scalar`` so that
    timeline relation properties resolve to their current (last-slot) value
    rather than the raw ``_timeline`` object.
    """
    return {
        s.name: _extract_scalar(s.type, values_map.get(s.id), s.config)
        for s in schemas
    }


def _serialise_formula_result(value: Any) -> Any:
    """
    Convert a formula result to a JSON-safe scalar.

    The formula engine returns native ``datetime`` objects for date functions
    (``now()``, ``today()``, ``dateAdd(…)`` etc.).  PostgreSQL JSONB does not
    accept Python datetimes, so we convert them to ISO 8601 strings here,
    immediately before persistence.  All other scalar types (int, float, str,
    bool, None) are returned unchanged.
    """
    from datetime import datetime as _DT  # local import to avoid circular risk
    if isinstance(value, _DT):
        return value.isoformat()
    return value


def _infer_result_type(value: Any) -> str:
    """
    Classify a formula result into one of four canonical type strings.

    Used to annotate stored formula values with ``result_type`` so that the
    filter system and frontend can apply type-appropriate operators without
    inspecting the expression itself.

    Returns one of: ``"date"``, ``"boolean"``, ``"number"``, ``"text"``.

    Note: ``bool`` must be checked before ``int``/``float`` because Python's
    ``bool`` is a subclass of ``int``.

    A date property referenced directly by a formula (``prop('date')``) yields a
    ``{"start", "end"}`` dict rather than a scalar; it is classified as ``"date"``
    so the filter UI offers date operators for such formula columns (#43).
    """
    from datetime import datetime as _DT
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, _DT):
        return "date"
    if isinstance(value, dict) and ("start" in value or "end" in value):
        return "date"
    if isinstance(value, (int, float)):
        return "number"
    return "text"


def _compute_formula(
    db: Session,
    schema: Any,
    entry_id: uuid.UUID,
    all_schemas: list[Any],
    values_map: dict[uuid.UUID, Optional[dict]],
) -> None:
    expression: str = (schema.config or {}).get("expression", "")
    if not expression:
        val = {"result": None, "error": "No expression configured"}
        repo.upsert_value(db, page_id=entry_id, schema_id=schema.id, value=val)
        values_map[schema.id] = val
        return

    ctx = _formula_context(all_schemas, values_map)
    fr = evaluate(expression, ctx)
    serialised = _serialise_formula_result(fr.result)

    # If the formula result is a list of relation UUIDs or already-resolved
    # entry descriptors (e.g. the formula passes through a relation prop or a
    # show_original rollup prop), normalise to a descriptor list and mark the
    # stored value with relation=True so FormulaCell renders clickable chips
    # instead of raw UUIDs (#20).
    def _resolve_entry(rid_str: str) -> Optional[dict]:
        try:
            target_uuid = uuid.UUID(rid_str)
        except (ValueError, TypeError):
            return None
        block = repo.get_block(db, target_uuid)
        if block is None or block.state != "active":
            return None
        return {
            "id": str(block.id),
            "title": (block.content or {}).get("title") or "",
            "database_id": str(block.parent_id) if block.parent_id else None,
        }

    resolved, is_relation = _resolve_formula_relation(serialised, _resolve_entry)

    val: dict = {
        "result": resolved,
        "result_type": _infer_result_type(fr.result),
    }
    if is_relation:
        val["relation"] = True
    if fr.style:
        val["style"] = fr.style
    if fr.error:
        val["error"] = fr.error
    repo.upsert_value(db, page_id=entry_id, schema_id=schema.id, value=val)
    values_map[schema.id] = val


def _compute_rollup(
    db: Session,
    schema: Any,
    entry_id: uuid.UUID,
    id_to_schema: dict[uuid.UUID, Any],
    values_map: dict[uuid.UUID, Optional[dict]],
) -> None:
    config = schema.config or {}
    rel_id_raw = config.get("relation_schema_id")
    roll_col_id_raw = config.get("rollup_schema_id")
    function: str = config.get("function", "count")

    def _store(result: Any, error: str | None = None, extra: dict | None = None) -> None:
        val: dict = {"result": result, "function": function}
        if error:
            val["error"] = error
        if extra:
            val.update(extra)
        repo.upsert_value(db, page_id=entry_id, schema_id=schema.id, value=val)
        values_map[schema.id] = val

    if not rel_id_raw or not roll_col_id_raw:
        _store(None, "Rollup not fully configured")
        return

    try:
        rel_schema_id = uuid.UUID(str(rel_id_raw))
        rollup_col_id = uuid.UUID(str(roll_col_id_raw))
    except ValueError:
        _store(None, "Invalid rollup configuration (bad UUID)")
        return

    # Read the relation value for this entry
    rel_value = values_map.get(rel_schema_id)
    if not rel_value:
        _store(_aggregate([], function))
        return

    # Timeline-aware: resolve current related_ids via schema config.
    rel_schema_obj = id_to_schema.get(rel_schema_id)
    related_ids_raw = _get_related_ids(rel_value, rel_schema_obj)
    related_ids: list[uuid.UUID] = []
    for rid in related_ids_raw:
        try:
            related_ids.append(uuid.UUID(str(rid)))
        except ValueError:
            continue

    if not related_ids:
        _store(_aggregate([], function))
        return

    # Exclude trashed or missing entries so that soft-deleted rows are never
    # counted or aggregated.  This is the single authoritative filter point;
    # all rollup call-sites benefit automatically.  SQLAlchemy's identity map
    # makes repeated get_block calls within the same session effectively free
    # after the first load of each block.
    active_related_ids = [
        rid for rid in related_ids
        if (b := repo.get_block(db, rid)) is not None and b.state == "active"
    ]

    if not active_related_ids:
        _store(_aggregate([], function))
        return

    # Batch-load values for all active related entries
    related_map = repo.list_values_for_pages(db, active_related_ids)

    # Resolve rollup column type
    rollup_col_schema = id_to_schema.get(rollup_col_id) or repo.get_schema(db, rollup_col_id)
    col_type = rollup_col_schema.type if rollup_col_schema else "text"
    rollup_col_config = rollup_col_schema.config if rollup_col_schema else None

    scalars: list[Any] = []
    for rid in active_related_ids:
        pv = next(
            (pv for pv in related_map.get(rid, []) if pv.property_schema_id == rollup_col_id),
            None,
        )
        scalars.append(_extract_scalar(col_type, pv.value if pv else None, rollup_col_config))

    # A formula / rollup target column whose stored values carry relation=True
    # holds relation-entry IDs (resolved above into ID lists). Treat it like a
    # native relation column so the raw-display chip path below applies, giving
    # clickable chips instead of [object Object] (and no chip for empty values).
    target_is_relation_formula = col_type in ("formula", "rollup") and any(
        isinstance(pv.value, dict) and pv.value.get("relation") is True
        for rid in active_related_ids
        for pv in related_map.get(rid, [])
        if pv.property_schema_id == rollup_col_id
    )

    # Relation-typed rollup targets yield lists of related entry IDs. For the
    # raw-display functions, resolve those IDs to entry descriptors so the cell
    # can render clickable relation chips instead of raw UUIDs (#11). Counting/
    # aggregating functions keep the ID lists so their semantics are unchanged.
    if (col_type in _RELATION_COL_TYPES or target_is_relation_formula) and function in _RAW_DISPLAY_FUNCTIONS:
        def _resolve_entry(rid_str: str) -> Optional[dict]:
            try:
                target_uuid = uuid.UUID(rid_str)
            except (ValueError, TypeError):
                return None
            block = repo.get_block(db, target_uuid)
            if block is None or block.state != "active":
                return None
            return {
                "id": str(block.id),
                "title": (block.content or {}).get("title") or "",
                "database_id": str(block.parent_id) if block.parent_id else None,
            }

        entries = _resolve_relation_entries(scalars, _resolve_entry)
        if function == "show_original":
            result: Any = entries
        elif function == "first_value":
            result = entries[0] if entries else None
        else:  # last_value
            result = entries[-1] if entries else None
        _store(result, extra={"relation": True})
        return

    # Date columns yield {"start", "end"} dicts (needed by the date
    # aggregations below and by formulas). The raw-display functions, however,
    # must emit canonical ISO strings the frontend can format — a dict would
    # render as "[object Object]". Convert each date scalar to its start
    # (falling back to end) ISO string before aggregating.
    if col_type == "date" and function in _RAW_DISPLAY_FUNCTIONS:
        iso_scalars = [
            (sc.get("start") or sc.get("end")) if isinstance(sc, dict) else sc
            for sc in scalars
        ]
        _store(_aggregate(iso_scalars, function))
        return

    _store(_aggregate(scalars, function))


# ─── Main entry point ─────────────────────────────────────────────────────────


def compute_all_for_entry(
    db: Session,
    database_id: uuid.UUID,
    entry_id: uuid.UUID,
) -> None:
    """
    Evaluate all formula and rollup schemas for *entry_id* and persist the
    results as PropertyValues.

    This function is idempotent – it overwrites any previously computed values.
    It does **not** call ``db.commit()``; that remains the caller's responsibility.

    Evaluation order is determined by a topological sort of the dependency
    graph.  If a cycle is detected, all computed schemas are written with an
    error result.
    """
    schemas = repo.list_schemas(db, database_id)
    computed_schemas = [s for s in schemas if s.type in ("formula", "rollup")]
    if not computed_schemas:
        return

    id_to_schema = {s.id: s for s in schemas}
    graph = build_dependency_graph(schemas)

    # Load all current values for this entry into a mutable map so that
    # downstream formulas can read freshly computed upstream results.
    values_list = repo.list_values(db, entry_id)
    values_map: dict[uuid.UUID, Optional[dict]] = {
        pv.property_schema_id: pv.value for pv in values_list
    }

    try:
        ordered_ids = topological_order(graph)
    except CycleError:
        for s in computed_schemas:
            repo.upsert_value(
                db,
                page_id=entry_id,
                schema_id=s.id,
                value={"result": None, "error": "Circular dependency detected"},
            )
        return

    for schema_id in ordered_ids:
        schema = id_to_schema.get(schema_id)
        if schema is None:
            continue
        if schema.type == "formula":
            _compute_formula(db, schema, entry_id, schemas, values_map)
        elif schema.type == "rollup":
            _compute_rollup(db, schema, entry_id, id_to_schema, values_map)


# ─── Cross-database cascade ───────────────────────────────────────────────────


def compute_cross_db_dependents(
    db: Session,
    source_database_id: uuid.UUID,
    changed_entry_id: uuid.UUID,
) -> list[uuid.UUID]:
    """
    Re-evaluate entries in *other* databases whose rollup schemas pull data
    from ``source_database_id`` and reference ``changed_entry_id``.

    Walk:
      1. For every database that is not ``source_database_id``:
      2.   For every rollup schema in that database:
      3.     Resolve its ``relation_schema_id`` → check whether the relation
             points to ``source_database_id``.
      4.     If yes, find entries whose relation value includes
             ``changed_entry_id`` and call ``compute_all_for_entry`` for them.

    Returns the list of database IDs (de-duplicated, excluding
    ``source_database_id``) in which at least one entry was recomputed.
    The caller is responsible for committing and broadcasting.
    """
    all_databases = repo.list_databases(db)
    affected_db_ids: list[uuid.UUID] = []
    changed_entry_id_str = str(changed_entry_id)

    for database in all_databases:
        if database.id == source_database_id:
            continue

        schemas = repo.list_schemas(db, database.id)

        # Collect rollup schemas that pull from source_database_id
        for schema in schemas:
            if schema.type != "rollup":
                continue
            config = schema.config or {}
            rel_id_raw = config.get("relation_schema_id")
            if not rel_id_raw:
                continue
            try:
                rel_schema_id = uuid.UUID(str(rel_id_raw))
            except ValueError:
                continue

            rel_schema = repo.get_schema(db, rel_schema_id)
            if rel_schema is None:
                continue
            target_raw = (rel_schema.config or {}).get("target_database_id")
            if not target_raw:
                continue
            try:
                target_db_id = uuid.UUID(str(target_raw))
            except ValueError:
                continue

            if target_db_id != source_database_id:
                continue

            # This rollup pulls from source_database_id via rel_schema_id.
            # Find entries in this database whose relation value contains
            # changed_entry_id.
            entries = repo.list_children(db, database.id, state="active")
            for entry in entries:
                rel_pv = repo.get_value(db, entry.id, rel_schema_id)
                if not rel_pv or not rel_pv.value:
                    continue
                if _entry_id_in_relation_value(changed_entry_id_str, rel_pv.value):
                    compute_all_for_entry(db, database.id, entry.id)
                    if database.id not in affected_db_ids:
                        affected_db_ids.append(database.id)

    return affected_db_ids


def compute_same_db_rollup_dependents(
    db: Session,
    database_id: uuid.UUID,
    changed_entry_id: uuid.UUID,
) -> bool:
    """
    Re-evaluate entries in the *same* database whose rollup schemas aggregate
    data from a self-referential relation that includes ``changed_entry_id``.

    ``compute_cross_db_dependents`` explicitly skips the source database, so
    this function fills that gap.  Whenever entry A changes, any sibling entry
    B in the same database that has a rollup pulling from A via a
    self-referential relation must also be recomputed.

    Returns ``True`` if at least one entry was recomputed.
    The caller is responsible for committing.
    """
    schemas = repo.list_schemas(db, database_id)
    schema_by_id: dict[uuid.UUID, Any] = {s.id: s for s in schemas}
    changed_entry_id_str = str(changed_entry_id)
    recomputed = False

    for schema in schemas:
        if schema.type != "rollup":
            continue
        config = schema.config or {}
        rel_id_raw = config.get("relation_schema_id")
        if not rel_id_raw:
            continue
        try:
            rel_schema_id = uuid.UUID(str(rel_id_raw))
        except ValueError:
            continue

        rel_schema = schema_by_id.get(rel_schema_id)
        if rel_schema is None:
            continue
        target_raw = (rel_schema.config or {}).get("target_database_id")
        if not target_raw:
            continue
        try:
            target_db_id = uuid.UUID(str(target_raw))
        except ValueError:
            continue

        if target_db_id != database_id:
            # Cross-database relation — handled by compute_cross_db_dependents.
            continue

        # Self-referential relation: find sibling entries that link to
        # changed_entry_id and recompute each one.
        entries = repo.list_children(db, database_id, state="active")
        for entry in entries:
            if entry.id == changed_entry_id:
                # Already recomputed by compute_all_for_entry — skip.
                continue
            rel_pv = repo.get_value(db, entry.id, rel_schema_id)
            if not rel_pv or not rel_pv.value:
                continue
            if _entry_id_in_relation_value(changed_entry_id_str, rel_pv.value):
                compute_all_for_entry(db, database_id, entry.id)
                recomputed = True

    return recomputed
