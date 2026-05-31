"""
Automation engine.

Three-layer architecture with strict separation of concerns:

  Receiver  -- validates and gates the incoming trigger event.
  Query     -- finds matching automations in the database.
  Execution -- resolves template variables and calls the service layer.

Receiver
--------
Entry point for all automation trigger events.  Currently only events with
``origin == "user"`` are processed; all other origins are silently rejected.

    # Future: replace this guard with sophisticated loop / cycle detection
    # so that ``origin == "automation"`` events can be handled safely with
    # a depth counter and chain-aware deduplication.

Query
-----
Two-step matching strategy keeps SQL simple and Python readable:

  1. SQL pre-filter: fetch all enabled automations owned by the event's
     database.  This throws away the bulk of irrelevant rows cheaply.
  2. Python fine-matcher: evaluate trigger fields against the event,
     honouring wildcards ("") and negations ("!<value>").
     An optional ``actor_filter`` field enables multi-user allow/deny logic.

Multi-trigger format
---------------------
The ``trigger`` JSON column stores either a single trigger dict (legacy) or
an array of trigger dicts (new format).  When stored as an array the engine
evaluates OR logic: the automation fires when the event matches ANY trigger.

Execution
---------
Actions are stored as ``{"endpoint": "METHOD /path/...", "body": {...}}``.
An optional ``"filter"`` key enables bulk entry updates:

  Legacy (no filter key):
    PUT /api/databases/{db_id}/entries/{entry_id}/values/{schema_id}
    -> upserts a property value on the single triggered entry

  Bulk (filter key present):
    PUT /api/databases/{db_id}/bulk-values/{schema_id}
    filter: {"mode": "all" | "where", "groups": [...]}
    -> upserts a property value on all (or filtered) entries in target db

Template variables resolved at execution time
---------------------------------------------
{trigger.entry_id}       UUID of the entry that fired the trigger
{trigger.db_uuid}        database UUID from the event
{trigger.property_uuid}  property schema UUID from the event
{trigger.new_value}      new cell value from the event
{today()}                current date as YYYY-MM-DD

Actor filter (optional trigger field)
--------------------------------------
The ``actor_filter`` JSON field extends the base ``actor_uuid`` wildcard
with multi-user allow/deny semantics:

    {
        "mode":               "specific",
        "entries":            [{"uuid": "<user-uuid>", "state": "positive"|"negative"}],
        "include_automation": false
    }

mode == "any"
    No actor filtering beyond the base ``actor_uuid`` check (wildcard).

mode == "specific"
    ``positive`` entries form an allow-list: if any positive entries exist,
    the actor must match at least one.
    ``negative`` entries form a deny-list: the actor must not match any.
    Both lists are evaluated independently and combined with AND logic.
    An empty entries list passes all actors.
"""
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.automations.automations_models import Automation
from app.automations.automations_repository import list_enabled_for_database
from app.blocks.models import Block, PropertyValue

logger = logging.getLogger(__name__)


# ─── Trigger event ────────────────────────────────────────────────────────────


@dataclass
class TriggerEvent:
    """
    Represents a single event that may fire one or more automations.

    All fields correspond to the seven-slot trigger signature.  Fields that
    are not applicable to a given event type should be left as empty strings.

    Attributes
    ----------
    action_type:    Semantic event label, e.g. ``"PropertyUpdate"``.
    origin:         ``"user"`` for human actions, ``"automation"`` for
                    engine-fired downstream events.
    actor_uuid:     UUID of the acting user or the automation that fired.
    db_uuid:        UUID of the affected database block.
    property_uuid:  UUID of the affected property schema.
    old_value:      Previous cell value serialised to string; ``""`` if unknown.
    new_value:      New cell value serialised to string; ``""`` if unknown.
    entry_id:       UUID of the affected entry; populated by the caller at
                    runtime, not stored as part of the trigger definition.
    """

    action_type:   str
    origin:        str
    actor_uuid:    str
    db_uuid:       str
    property_uuid: str
    old_value:     str
    new_value:     str
    entry_id:      str = field(default="")


# ─── Layer 1 — Receiver ───────────────────────────────────────────────────────


async def receive(event: TriggerEvent, db: Session) -> None:
    """
    Gate and dispatch an incoming trigger event.

    Only events with ``origin == "user"`` are processed.  All other origins
    are rejected with a silent return.

    # Future: replace this guard with sophisticated loop / cycle detection
    # so that ``origin == "automation"`` events can be handled safely with
    # a depth counter and chain-aware deduplication.
    """
    if event.origin != "user":
        return

    matches = _query(event, db)
    if not matches:
        return

    for automation in matches:
        await _execute(automation, event, db)


# ─── Layer 2 — Query ─────────────────────────────────────────────────────────


def _query(event: TriggerEvent, db: Session) -> list[Automation]:
    """
    Find all enabled automations whose trigger matches *event*.

    Returns an empty list when ``event.db_uuid`` is missing or invalid —
    a guard against malformed events propagating further into the engine.
    """
    if not event.db_uuid:
        return []

    try:
        db_id = uuid.UUID(event.db_uuid)
    except ValueError:
        logger.warning(
            "Automation engine received invalid db_uuid: %r", event.db_uuid
        )
        return []

    candidates = list_enabled_for_database(db, db_id)
    return [a for a in candidates if _matches_trigger(a.trigger, event)]


def _matches_trigger(trigger_field: Any, event: TriggerEvent) -> bool:
    """
    Evaluate the stored trigger field against *event*.

    Handles both the legacy single-dict format and the new multi-trigger array
    format.  When stored as a list, the automation fires when ANY element
    matches (OR semantics).

    Parameters
    ----------
    trigger_field:
        The raw value of the ``trigger`` JSON column — either a dict or a list.
    event:
        The incoming trigger event to match against.
    """
    if isinstance(trigger_field, list):
        return any(_matches(t, event) for t in trigger_field)
    if isinstance(trigger_field, dict):
        return _matches(trigger_field, event)
    return False


def _matches(trigger: dict, event: TriggerEvent) -> bool:
    """
    Return True when every field in *trigger* matches the corresponding
    field on *event*.

    Base matching rules per scalar field
    -------------------------------------
    ``""``        -> wildcard; matches any value.
    ``"!<v>"``    -> negation; matches when event value is not ``<v>``.
    ``"<v>"``     -> exact; matches only when event value equals ``<v>``.

    Extended actor filter
    ----------------------
    When ``trigger["actor_filter"]["mode"] == "specific"``, the entries list
    is evaluated after the base checks:
      - Positive entries: actor must match at least one (if any exist).
      - Negative entries: actor must not match any.
    ``mode == "any"`` or an absent ``actor_filter`` field is a no-op.
    """
    pairs: list[tuple[str, str]] = [
        (trigger.get("action_type",   ""), event.action_type),
        (trigger.get("origin",        ""), event.origin),
        (trigger.get("actor_uuid",    ""), event.actor_uuid),
        (trigger.get("db_uuid",       ""), event.db_uuid),
        (trigger.get("property_uuid", ""), event.property_uuid),
        (trigger.get("old_value",     ""), event.old_value),
        (trigger.get("new_value",     ""), event.new_value),
    ]
    for stored, actual in pairs:
        if stored == "":
            continue  # wildcard
        if stored.startswith("!"):
            if actual == stored[1:]:
                return False  # negation matched — exclude
        else:
            if actual != stored:
                return False  # exact mismatch

    # Extended actor filter — evaluated after the basic scalar checks.
    actor_filter = trigger.get("actor_filter")
    if actor_filter and actor_filter.get("mode") == "specific":
        if not _matches_actor_filter(actor_filter, event.actor_uuid):
            return False

    return True


def _matches_actor_filter(actor_filter: dict, actor_uuid: str) -> bool:
    """
    Evaluate the extended actor_filter against *actor_uuid*.

    Positive entries form an allow-list; if present, the actor must match
    at least one.  Negative entries form a deny-list; the actor must not
    match any.  An empty entries list passes all actors.
    """
    entries: list[dict] = actor_filter.get("entries", [])
    positive = [e["uuid"] for e in entries if e.get("state") == "positive"]
    negative = [e["uuid"] for e in entries if e.get("state") == "negative"]

    # Allow-list: if any positive entries exist, actor must be in the list.
    if positive and actor_uuid not in positive:
        return False

    # Deny-list: actor must not appear in the negative list.
    if actor_uuid in negative:
        return False

    return True


# ─── Layer 3 — Execution ─────────────────────────────────────────────────────

# Compiled regex for each supported endpoint pattern.  Named groups map
# directly to the handler's keyword arguments.
_PUT_VALUES_RE = re.compile(
    r"^PUT /api/databases/(?P<db_id>[^/]+)"
    r"/entries/(?P<entry_id>[^/]+)"
    r"/values/(?P<schema_id>[^/]+)$"
)

# Bulk endpoint: PUT /api/databases/<db_id>/bulk-values/<schema_id>
# Used by EditProperty actions with a filter specifier.
_BULK_PUT_VALUES_RE = re.compile(
    r"^PUT /api/databases/(?P<db_id>[^/]+)"
    r"/bulk-values/(?P<schema_id>[^/]+)$"
)


async def _execute(automation: Automation, event: TriggerEvent, db: Session) -> None:
    """
    Resolve template variables and dispatch each action in *automation*.

    Actions are executed sequentially.  A failure in one action is logged
    but does not prevent subsequent actions from running.

    Dispatch routing
    -----------------
    If an action contains a ``"filter"`` key, it is routed to
    ``_dispatch_bulk`` which handles filtered bulk-entry updates.
    Actions without a filter key use the legacy single-entry dispatch path.
    """
    ctx = _build_context(event)

    for action in automation.actions:
        try:
            endpoint_raw: str = action.get("endpoint", "")
            body_raw: Any = action.get("body", {})
            filter_spec: Optional[dict] = action.get("filter")

            endpoint = _render(endpoint_raw, ctx)
            body = _render_body(body_raw, ctx)

            if filter_spec is not None:
                await _dispatch_bulk(endpoint, filter_spec, body, db)
            else:
                await _dispatch(endpoint, body, db)

        except Exception as exc:
            logger.error(
                "Automation %s: action failed — endpoint=%r error=%s",
                automation.id,
                action.get("endpoint"),
                exc,
                exc_info=True,
            )


def _build_context(event: TriggerEvent) -> dict[str, str]:
    """Build the template variable substitution map for *event*."""
    return {
        "trigger.entry_id":      event.entry_id,
        "trigger.db_uuid":       event.db_uuid,
        "trigger.property_uuid": event.property_uuid,
        "trigger.new_value":     event.new_value,
        "today()":               date.today().isoformat(),
    }


def _render(template: str, ctx: dict[str, str]) -> str:
    """Replace all ``{key}`` placeholders in *template* from *ctx*."""
    for key, value in ctx.items():
        template = template.replace(f"{{{key}}}", value)
    return template


def _render_body(obj: Any, ctx: dict[str, str]) -> Any:
    """Recursively render template variables inside a JSON-serialisable object."""
    if isinstance(obj, str):
        return _render(obj, ctx)
    if isinstance(obj, dict):
        return {k: _render_body(v, ctx) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_render_body(item, ctx) for item in obj]
    return obj


async def _dispatch(endpoint: str, body: Any, db: Session) -> None:
    """
    Map a resolved endpoint string to the corresponding service-layer call.

    Raises ``ValueError`` when the endpoint pattern is not registered.
    """
    m = _PUT_VALUES_RE.match(endpoint)
    if m:
        await _handle_upsert_value(
            db=db,
            db_uuid=m.group("db_id"),
            entry_id=m.group("entry_id"),
            schema_id=m.group("schema_id"),
            body=body,
        )
        return

    raise ValueError(f"No handler registered for endpoint: {endpoint!r}")


async def _dispatch_bulk(
    endpoint: str,
    filter_spec: dict,
    body: Any,
    db: Session,
) -> None:
    """
    Dispatch a bulk-update action to the filtered entry handler.

    Parses the bulk endpoint pattern and delegates to
    ``_handle_bulk_upsert_value`` which resolves the filter and iterates
    over matching entries.

    Raises ``ValueError`` when the endpoint pattern is not recognised.
    """
    m = _BULK_PUT_VALUES_RE.match(endpoint)
    if m:
        await _handle_bulk_upsert_value(
            db=db,
            db_uuid=m.group("db_id"),
            schema_id=m.group("schema_id"),
            filter_spec=filter_spec,
            body=body,
        )
        return

    raise ValueError(f"No bulk handler registered for endpoint: {endpoint!r}")


async def _handle_upsert_value(
    db: Session,
    db_uuid: str,
    entry_id: str,
    schema_id: str,
    body: Any,
) -> None:
    """
    Execute a property-value upsert on a single entry as a direct service-layer call.

    Commits its own transaction and broadcasts ``database_entries_updated``
    so open table views refresh without a full page reload — identical
    behaviour to the regular HTTP upsert endpoint.
    """
    from app.blocks import repository as repo
    from app.blocks.computed import (
        compute_all_for_entry,
        compute_same_db_rollup_dependents,
    )
    from app.ws.broadcaster import broadcast_block_event

    try:
        db_id   = uuid.UUID(db_uuid)
        page_id = uuid.UUID(entry_id)
        s_id    = uuid.UUID(schema_id)
    except ValueError as exc:
        raise ValueError(
            f"Automation action contains invalid UUID: {exc}"
        ) from exc

    value: Optional[dict] = body.get("value") if isinstance(body, dict) else None

    try:
        repo.upsert_value(db, page_id=page_id, schema_id=s_id, value=value)
        compute_all_for_entry(db, db_id, page_id)
        compute_same_db_rollup_dependents(db, db_id, page_id)
        db.commit()
    except Exception:
        db.rollback()
        raise

    await broadcast_block_event(
        event_type="database_entries_updated",
        block_id=db_uuid,
        before=None,
        after={"database_id": db_uuid},
    )


async def _handle_bulk_upsert_value(
    db: Session,
    db_uuid: str,
    schema_id: str,
    filter_spec: dict,
    body: Any,
) -> None:
    """
    Execute a property-value upsert on all entries in *db_uuid* that match
    *filter_spec*.

    filter_spec shape
    -----------------
    {
        "mode":   "all" | "where",
        "groups": [
            {
                "conjunction": "and" | "or",
                "filters": [
                    {"schemaId": "<uuid>", "operator": "<op>", "value": "<v>"}
                ]
            }
        ]
    }

    mode == "all"   -- every active entry in the database is updated.
    mode == "where" -- only entries whose property values satisfy all groups
                       (groups are ANDed; conditions within a group follow the
                       group's own conjunction) are updated.

    Each entry is updated, computed and committed individually so that a
    failure on one entry is isolated and does not roll back the others.
    A single ``database_entries_updated`` broadcast is emitted after the loop.
    """
    from app.blocks import repository as repo
    from app.blocks.computed import (
        compute_all_for_entry,
        compute_same_db_rollup_dependents,
    )
    from app.ws.broadcaster import broadcast_block_event

    try:
        db_id = uuid.UUID(db_uuid)
        s_id  = uuid.UUID(schema_id)
    except ValueError as exc:
        raise ValueError(
            f"Automation bulk action contains invalid UUID: {exc}"
        ) from exc

    # Fetch all active entries in the target database.
    rows = db.execute(
        sa.select(Block).where(
            Block.parent_id == db_id,
            Block.state == "active",
        )
    ).scalars().all()

    logger.info(
        "Bulk action: db=%s schema=%s mode=%s entries_found=%d",
        db_uuid, schema_id, filter_spec.get("mode"), len(rows),
    )

    mode   = filter_spec.get("mode", "all")
    groups = filter_spec.get("groups", [])

    if mode == "where" and groups:
        rows = _filter_entries(db, rows, groups)
        logger.info("Bulk action: entries_after_filter=%d", len(rows))

    if not rows:
        logger.info("Bulk action: no entries to update (filter matched nothing or DB is empty)")
        return

    value: Optional[dict] = body.get("value") if isinstance(body, dict) else None

    for row in rows:
        try:
            repo.upsert_value(db, page_id=row.id, schema_id=s_id, value=value)
            compute_all_for_entry(db, db_id, row.id)
            compute_same_db_rollup_dependents(db, db_id, row.id)
            db.commit()
            logger.info("Bulk action: updated entry %s", row.id)
        except Exception as exc:
            db.rollback()
            logger.error(
                "Bulk action failed on entry %s — schema=%s error=%s",
                row.id,
                schema_id,
                exc,
                exc_info=True,
            )

    await broadcast_block_event(
        event_type="database_entries_updated",
        block_id=db_uuid,
        before=None,
        after={"database_id": db_uuid},
    )


# ─── Filter helpers ───────────────────────────────────────────────────────────


def _filter_entries(
    db: Session,
    entries: list,
    groups: list[dict],
) -> list:
    """
    Filter *entries* in-memory using the stored filter groups.

    Loads property values for all entries in a single query, then applies
    each group's conditions.  Groups are ANDed together; within a group,
    conditions are combined with the group's own conjunction.

    Returns the subset of entries that satisfy all groups.
    """
    if not entries or not groups:
        return entries

    entry_ids = [e.id for e in entries]

    pv_rows = db.execute(
        sa.select(PropertyValue).where(
            PropertyValue.page_id.in_(entry_ids)
        )
    ).scalars().all()

    logger.debug("Filter: loaded %d property values for %d entries", len(pv_rows), len(entry_ids))

    # Build lookup: str(entry_id) -> {str(schema_id): raw_value}
    # PropertyValue uses ``property_schema_id`` as the FK column name.
    val_map: dict[str, dict[str, Any]] = {}
    for pv in pv_rows:
        eid = str(pv.page_id)
        sid = str(pv.property_schema_id)
        if eid not in val_map:
            val_map[eid] = {}
        val_map[eid][sid] = pv.value

    result = []
    for entry in entries:
        ev = val_map.get(str(entry.id), {})
        # All groups must match (groups are ANDed).
        matched = all(_group_matches(ev, g) for g in groups)
        logger.debug("Filter: entry %s -> match=%s", entry.id, matched)
        if matched:
            result.append(entry)
    return result


def _group_matches(entry_values: dict[str, Any], group: dict) -> bool:
    """
    Evaluate a single filter group against *entry_values*.

    ``conjunction == "and"`` requires all conditions to match.
    ``conjunction == "or"``  requires at least one condition to match.
    An empty conditions list always passes.
    """
    conjunction = group.get("conjunction", "and")
    filters: list[dict] = group.get("filters", [])
    if not filters:
        return True
    results = [_condition_matches(entry_values, f) for f in filters]
    if conjunction == "or":
        return any(results)
    return all(results)


def _condition_matches(entry_values: dict[str, Any], condition: dict) -> bool:
    """Evaluate a single filter condition against the entry's property values."""
    schema_id    = condition.get("schemaId", "")
    operator     = condition.get("operator", "eq")
    filter_value = condition.get("value", "")

    raw      = entry_values.get(schema_id)
    cell_str = _extract_cell_string(raw)

    return _compare(cell_str, operator, filter_value)


def _extract_cell_string(raw: Any) -> str:
    """
    Extract a comparable string representation from a stored cell value.

    Cell values are JSON objects whose shape depends on property type:
      text / email / phone / url -> {"text": "<value>"}
      select                     -> {"option": "<value>"}
      number                     -> {"number": <int|float>}
      checkbox                   -> {"checked": <bool>}

    Number normalisation: whole floats (2.0) are returned as "2" so that a
    filter value of "2" matches regardless of whether the stored JSON number
    is an int or a float.

    Checkbox normalisation: booleans are lowercased ("true"/"false") to
    match the string values used by the frontend's filter option elements.

    Falls back to ``str(raw)`` for unknown shapes, and returns ``""`` for
    None or missing values.
    """
    if raw is None:
        return ""
    if isinstance(raw, dict):
        if "text" in raw:
            v = raw["text"]
            return str(v) if v is not None else ""
        if "option" in raw:
            v = raw["option"]
            return str(v) if v is not None else ""
        if "number" in raw:
            v = raw["number"]
            if v is None:
                return ""
            # Normalise whole floats (e.g. 2.0 → "2") so string comparison
            # works when the user types "2" in the filter input.
            try:
                f = float(v)
                return str(int(f)) if f == int(f) else str(f)
            except (ValueError, TypeError):
                return str(v)
        if "checked" in raw:
            v = raw["checked"]
            # Lowercase so "true"/"false" matches <option value="true|false">
            return str(v).lower() if v is not None else ""
    return str(raw)


def _compare(cell_str: str, operator: str, filter_value: str) -> bool:
    """
    Compare *cell_str* against *filter_value* using *operator*.

    For ``eq`` and ``neq``, a numeric comparison is attempted first so that
    normalised number strings ("2") match filter inputs ("2.0") and vice-versa.
    String comparison is the fallback when either value is non-numeric.

    Supports all FilterOperator values used by the frontend filter panel.
    Unknown operators pass by default to avoid silently dropping updates
    when new operators are introduced before the engine is updated.
    """
    if operator in ("eq", "neq"):
        # Try numeric comparison first (handles "2" == "2.0" etc.)
        try:
            cell_num   = float(cell_str)
            filter_num = float(filter_value)
            numeric_eq = cell_num == filter_num
            return numeric_eq if operator == "eq" else not numeric_eq
        except (ValueError, TypeError):
            pass
        # Fallback: plain string comparison
        return (cell_str == filter_value) if operator == "eq" else (cell_str != filter_value)
    if operator == "contains":
        return filter_value.lower() in cell_str.lower()
    if operator == "not_contains":
        return filter_value.lower() not in cell_str.lower()
    if operator == "starts_with":
        return cell_str.lower().startswith(filter_value.lower())
    if operator == "ends_with":
        return cell_str.lower().endswith(filter_value.lower())
    if operator == "is_empty":
        return cell_str == ""
    if operator == "is_not_empty":
        return cell_str != ""
    if operator in ("gt", "gte", "lt", "lte"):
        try:
            cell_num   = float(cell_str)
            filter_num = float(filter_value)
        except (ValueError, TypeError):
            return False
        if operator == "gt":  return cell_num > filter_num
        if operator == "gte": return cell_num >= filter_num
        if operator == "lt":  return cell_num < filter_num
        if operator == "lte": return cell_num <= filter_num

    # Unknown operator — pass through to avoid silently blocking updates.
    logger.debug("Unknown filter operator %r — condition passes by default", operator)
    return True
