"""
Option identity for choice properties and relation nuances.

A select property (single or multiple choice) and the nuance of a relation
offer a list of options. Each option has a stable ``id``, a ``label`` and an
optional ``color``::

    {"id": "<uuid>", "label": "Text", "color": "blue"}

Values store only ids: ``{"option": id}`` for a single choice,
``{"options": [id, ...]}`` for a multiple choice, and nuance maps
``{entry_id: id}``. Renaming an option therefore changes its label in the
property's config and nothing else, and every place that shows the option
shows the new label at once.

This module owns the rules around that:

* ``normalize_config`` gives every option an id when the config is written,
  keeps the ids a client already knows, and drops empty and duplicate labels.
  An option sent without id but with the label of an existing option keeps
  that option's id, so a client that does not know ids yet cannot orphan the
  values by accident.
* ``removed_ids`` and the ``purge_*`` functions remove a deleted option from
  everything that refers to it: entry and template values, view filters and
  automations. Nothing keeps an id whose option is gone.
* ``count_usage`` tells a delete confirmation how many entries use an option.
* ``label_map`` turns ids back into labels for formulas, rollups and sorting.

Bilateral relations share their nuance options between both sides, ids
included; the router copies them to the mirror together with the rest of the
nuance config.
"""
import uuid
from typing import Any, Iterable, Optional

from sqlalchemy import String, cast, or_, select
from sqlalchemy.orm import Session

from app.automations.automations_models import Automation
from app.blocks.models import Block, PropertySchema, PropertyValue

SELECT_TYPE = "select"
RELATION_TYPE = "relation"

# Config key of the sort toggle: sort by the configured option order instead
# of alphabetically by label.
MANUAL_SORT_KEY = "manualSortOrder"


# ─── Config ───────────────────────────────────────────────────────────────────


def _new_id() -> str:
    return str(uuid.uuid4())


def normalize_options(raw: Any, previous: Optional[Iterable[Any]] = None) -> list[dict]:
    """
    The options of *raw* in canonical form.

    * Every option gets an id. A missing or repeated id is replaced; an option
      without id whose label matches an option of *previous* gets that id.
    * Labels are trimmed; empty labels and repeated labels are dropped (the
      first occurrence wins).
    * A legacy plain string becomes an option with that label.
    """
    known: dict[str, str] = {}
    for item in normalize_options_loose(previous):
        known.setdefault(item["label"], item["id"])

    result: list[dict] = []
    seen_ids: set[str] = set()
    seen_labels: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str):
            label, option_id, color = item, None, None
        elif isinstance(item, dict):
            label, option_id, color = item.get("label"), item.get("id"), item.get("color")
        else:
            continue
        if not isinstance(label, str):
            continue
        label = label.strip()
        if not label or label in seen_labels:
            continue
        if not isinstance(option_id, str) or not option_id or option_id in seen_ids:
            option_id = known.get(label)
            if option_id is None or option_id in seen_ids:
                option_id = _new_id()
        option = {"id": option_id, "label": label}
        if isinstance(color, str) and color:
            option["color"] = color
        result.append(option)
        seen_ids.add(option_id)
        seen_labels.add(label)
    return result


def normalize_options_loose(raw: Any) -> list[dict]:
    """The options of *raw* that already carry an id and a label, unchanged."""
    result = []
    for item in raw if isinstance(raw, list) else []:
        if (
            isinstance(item, dict)
            and isinstance(item.get("id"), str) and item["id"]
            and isinstance(item.get("label"), str) and item["label"].strip()
        ):
            result.append({**item, "label": item["label"].strip()})
    return result


def _nuance_options(config: Optional[dict]) -> Any:
    nuance = (config or {}).get("nuance")
    return nuance.get("options") if isinstance(nuance, dict) else None


def normalize_config(
    schema_type: str,
    config: Optional[dict],
    previous: Optional[dict] = None,
) -> Optional[dict]:
    """
    *config* with its option lists in canonical form.

    Select properties carry ``options``; relations may carry
    ``nuance.options``. Other types are returned unchanged. *previous* is the
    config before the change, used to keep the ids of options a client sent
    without one.
    """
    if config is None:
        return None
    result = dict(config)
    if schema_type == SELECT_TYPE:
        result["options"] = normalize_options(
            result.get("options"), (previous or {}).get("options"),
        )
    elif schema_type == RELATION_TYPE:
        nuance = result.get("nuance")
        if isinstance(nuance, dict) and "options" in nuance:
            result["nuance"] = {
                **nuance,
                "options": normalize_options(nuance.get("options"), _nuance_options(previous)),
            }
    return result


def option_list(schema_type: str, config: Optional[dict]) -> list[dict]:
    """The options a property offers: select options or nuance options."""
    if schema_type == SELECT_TYPE:
        return normalize_options_loose((config or {}).get("options"))
    if schema_type == RELATION_TYPE:
        return normalize_options_loose(_nuance_options(config))
    return []


def label_map(options: Iterable[dict]) -> dict[str, str]:
    """Option id to label."""
    return {o["id"]: o["label"] for o in options}


def removed_ids(schema_type: str, old: Optional[dict], new: Optional[dict]) -> set[str]:
    """Ids of options *old* offers and *new* no longer does."""
    before = {o["id"] for o in option_list(schema_type, old)}
    after = {o["id"] for o in option_list(schema_type, new)}
    return before - after


def labels_changed(schema_type: str, old: Optional[dict], new: Optional[dict]) -> bool:
    """True when an option was renamed or removed, so stored labels went stale."""
    before = label_map(option_list(schema_type, old))
    after = label_map(option_list(schema_type, new))
    return any(after.get(option_id) != label for option_id, label in before.items())


# ─── Values ───────────────────────────────────────────────────────────────────


def _strip_choice(value: dict, removed: set[str]) -> Optional[dict]:
    """A flat select value without the removed ids; None when nothing is left."""
    if "option" in value:
        return None if value.get("option") in removed else value
    if "options" in value:
        kept = [o for o in value.get("options") or [] if o not in removed]
        if kept == list(value.get("options") or []):
            return value
        return {**value, "options": kept} if kept else None
    return value


def strip_select_value(value: Any, removed: set[str]) -> Any:
    """
    A stored select value without the ids in *removed*.

    Handles flat values and timelines; a timeline slot left empty is dropped.
    Returns None when nothing remains.
    """
    if not isinstance(value, dict) or not removed:
        return value
    if "_timeline" in value:
        timeline = value.get("_timeline")
        if not isinstance(timeline, dict):
            return value
        slots = {}
        for period, slot in timeline.items():
            if not isinstance(slot, dict):
                slots[period] = slot
                continue
            kept = _strip_choice(slot, removed)
            if kept is not None:
                slots[period] = kept
        if slots == timeline:
            return value
        return {**value, "_timeline": slots} if slots else None
    return _strip_choice(value, removed)


def _strip_nuance_map(nuances: Any, removed: set[str]) -> Any:
    if not isinstance(nuances, dict):
        return nuances
    return {uid: n for uid, n in nuances.items() if n not in removed}


def strip_nuance_value(value: Any, removed: set[str]) -> Any:
    """
    A stored relation value without nuances whose option is in *removed*.

    The links stay; only their nuance goes. Covers the flat ``nuances`` map,
    the ``nuancePool`` of a timelined relation and the ``nuances`` of every
    timeline slot. Empty maps are removed.
    """
    if not isinstance(value, dict) or not removed:
        return value
    result = dict(value)

    if "nuances" in result:
        kept = _strip_nuance_map(result["nuances"], removed)
        if kept:
            result["nuances"] = kept
        else:
            del result["nuances"]

    if isinstance(result.get("nuancePool"), dict):
        pool = {}
        for uid, ranges in result["nuancePool"].items():
            if isinstance(ranges, dict):
                kept = {r: n for r, n in ranges.items() if n not in removed}
                if kept:
                    pool[uid] = kept
        if pool:
            result["nuancePool"] = pool
        else:
            del result["nuancePool"]

    if isinstance(result.get("_timeline"), dict):
        timeline = {}
        for period, slot in result["_timeline"].items():
            if isinstance(slot, dict) and "nuances" in slot:
                slot = dict(slot)
                kept = _strip_nuance_map(slot["nuances"], removed)
                if kept:
                    slot["nuances"] = kept
                else:
                    del slot["nuances"]
            timeline[period] = slot
        result["_timeline"] = timeline

    return result


def _values_mentioning(db: Session, schema_id: uuid.UUID, ids: Iterable[str]):
    """
    The stored values of *schema_id* whose JSON text contains one of *ids*.

    Ids are UUIDs, so a text match is exact enough to narrow the rows; the
    caller still decides per value.
    """
    ids = list(ids)
    if not ids:
        return []
    text = cast(PropertyValue.value, String)
    clauses = [text.contains(f'"{option_id}"', autoescape=True) for option_id in ids]
    stmt = select(PropertyValue).where(
        PropertyValue.property_schema_id == schema_id, or_(*clauses),
    )
    return list(db.scalars(stmt))


def purge_values(db: Session, schema: PropertySchema, removed: set[str]) -> int:
    """
    Remove the options in *removed* from every stored value of *schema*,
    entries and templates alike. Returns the number of values changed.
    """
    if not removed:
        return 0
    strip = strip_select_value if schema.type == SELECT_TYPE else strip_nuance_value
    changed = 0
    for pv in _values_mentioning(db, schema.id, removed):
        new_value = strip(pv.value, removed)
        if new_value != pv.value:
            pv.value = new_value
            changed += 1
    db.flush()
    return changed


def count_usage(db: Session, schema: PropertySchema, option_id: str) -> int:
    """Number of active entries of *schema*'s database that use *option_id*."""
    removed = {option_id}
    strip = strip_select_value if schema.type == SELECT_TYPE else strip_nuance_value
    rows = _values_mentioning(db, schema.id, removed)
    if not rows:
        return 0
    active = set(db.scalars(
        select(Block.id).where(
            Block.id.in_([pv.page_id for pv in rows]),
            Block.state == "active",
            Block.type != "entry_template",
        )
    ))
    return sum(
        1 for pv in rows
        if pv.page_id in active and strip(pv.value, removed) != pv.value
    )


# ─── Filters and automations ──────────────────────────────────────────────────


def strip_filter_groups(
    groups: Any, schema_id: str, removed: set[str],
) -> tuple[Any, bool]:
    """
    *groups* without conditions on *schema_id* that test a removed option.
    Groups left without conditions are dropped. Returns the groups and whether
    anything changed.
    """
    if not isinstance(groups, list) or not removed:
        return groups, False
    result = []
    changed = False
    for group in groups:
        if not isinstance(group, dict):
            result.append(group)
            continue
        filters = group.get("filters")
        if not isinstance(filters, list):
            result.append(group)
            continue
        kept = [
            f for f in filters
            if not (
                isinstance(f, dict)
                and f.get("schemaId") == schema_id
                and f.get("value") in removed
            )
        ]
        if len(kept) != len(filters):
            changed = True
            if kept:
                result.append({**group, "filters": kept})
        else:
            result.append(group)
    return result, changed


def _action_schema_id(action: dict) -> Optional[str]:
    """The property an action writes, taken from the last path segment."""
    endpoint = action.get("endpoint")
    if not isinstance(endpoint, str):
        return None
    tail = endpoint.rstrip("/").rsplit("/", 1)[-1]
    try:
        return str(uuid.UUID(tail))
    except ValueError:
        return None


def purge_automations(db: Session, schema_id: uuid.UUID, removed: set[str]) -> int:
    """
    Remove the options in *removed* from every automation: conditions on the
    property that test one, and actions that would write one. Returns the
    number of automations changed.
    """
    if not removed:
        return 0
    key = str(schema_id)
    changed_count = 0
    for automation in db.scalars(select(Automation)):
        actions = automation.actions if isinstance(automation.actions, list) else []
        new_actions = []
        changed = False
        for action in actions:
            if not isinstance(action, dict):
                new_actions.append(action)
                continue
            action = dict(action)
            spec = action.get("filter")
            if isinstance(spec, dict):
                groups, groups_changed = strip_filter_groups(spec.get("groups"), key, removed)
                if groups_changed:
                    action["filter"] = {**spec, "groups": groups}
                    changed = True
            body = action.get("body")
            if _action_schema_id(action) == key and isinstance(body, dict) and "value" in body:
                value = body.get("value")
                stripped = strip_select_value(value, removed)
                if stripped != value:
                    action["body"] = {**body, "value": stripped}
                    changed = True
            new_actions.append(action)
        if changed:
            automation.actions = new_actions
            changed_count += 1
    db.flush()
    return changed_count
