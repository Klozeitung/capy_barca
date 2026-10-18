"""give choice options and nuances stable ids

Revision ID: t0w2v1r30020
Revises: s9v1u0q20019
Create Date: 2026-10-17

Until now a select property stored its options as labels, and every value
stored the label it chose: ``{"option": "Text"}``, ``{"options": [...]}``.
The nuances of a relation did the same. A label could therefore never change
without orphaning every value that used it.

This revision gives every option an id and stores ids instead of labels:

property_schemas.config
    Select properties: ``options`` becomes ``[{"id", "label", "color"?}]``.
    Plain string options become objects. Empty and repeated labels are
    dropped.
    Relations: ``nuance.options`` the same. The two sides of a bilateral
    relation share their option set, so both sides get the same ids.

property_values.value (entries and templates)
    Select: ``option`` / ``options`` / legacy ``selected`` hold ids, also
    inside every ``_timeline`` slot. A legacy ``selected`` becomes ``option``
    or ``options``.
    Relations: ``nuances``, ``nuancePool`` and the ``nuances`` of every
    ``_timeline`` slot hold ids.

database_views.filter_groups
    Conditions on a select property compare against ids.

automations.actions
    Conditions on a select property compare against ids; actions that write
    a select value write ids.

Labels that no option carries any more, left behind when options were
deleted before deleting cleaned up, are removed: from values, from view
filters (the condition goes) and from automations. Every removal is logged
per property, so the upgrade output says what went.

Downgrade
---------
Not supported. This is a data migration; the way back is restoring a backup.
"""
import json
import logging
import uuid
from typing import Any, Optional, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "t0w2v1r30020"
down_revision: Union[str, None] = "s9v1u0q20019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")


# ─── Conversion (pure, no database access) ────────────────────────────────────


class Report:
    """Collects what the conversion removed, for the upgrade's output."""

    def __init__(self) -> None:
        self.dropped: list[str] = []

    def drop(self, where: str, what: str) -> None:
        self.dropped.append(f"{where}: {what}")


def assign_ids(raw: Any, known: Optional[dict[str, str]] = None) -> list[dict]:
    """
    Options of *raw* with ids. *known* maps labels to ids that must be reused,
    which is how both sides of a bilateral relation end up with the same ids.
    """
    known = known or {}
    result: list[dict] = []
    seen_labels: set[str] = set()
    seen_ids: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str):
            label, option_id, color = item, None, None
        elif isinstance(item, dict):
            label, option_id, color = item.get("label"), item.get("id"), item.get("color")
        else:
            continue
        if not isinstance(label, str) or not label.strip():
            continue
        label = label.strip()
        if label in seen_labels:
            continue
        if not isinstance(option_id, str) or not option_id or option_id in seen_ids:
            option_id = known.get(label) or str(uuid.uuid4())
        option = {"id": option_id, "label": label}
        if isinstance(color, str) and color:
            option["color"] = color
        result.append(option)
        seen_labels.add(label)
        seen_ids.add(option_id)
    return result


def lookup(options: list[dict]) -> dict[str, str]:
    """Label to id, plus every id to itself, so a re-run changes nothing."""
    result = {o["label"]: o["id"] for o in options}
    result.update({o["id"]: o["id"] for o in options})
    return result


def _convert_choice(slot: dict, ids: dict[str, str], unknown: set[str]) -> Optional[dict]:
    """A flat select payload with ids; None when nothing usable remains."""
    result = {k: v for k, v in slot.items() if k not in ("option", "options", "selected")}
    if "option" in slot:
        raw: Any = slot["option"]
    elif "options" in slot:
        raw = slot["options"]
    elif "selected" in slot:
        raw = slot["selected"]
    else:
        return slot

    multiple = isinstance(raw, list) or ("options" in slot and "option" not in slot)
    labels = raw if isinstance(raw, list) else ([] if raw in (None, "") else [raw])
    converted = []
    for label in labels:
        if not isinstance(label, str):
            continue
        option_id = ids.get(label)
        if option_id is None:
            unknown.add(label)
        elif option_id not in converted:
            converted.append(option_id)

    if not converted:
        return None if not result else result
    if multiple:
        result["options"] = converted
    else:
        result["option"] = converted[0]
    return result


def convert_select_value(value: Any, ids: dict[str, str], unknown: set[str]) -> Any:
    """A stored select value with ids; None when nothing usable remains."""
    if not isinstance(value, dict):
        return value
    if "_timeline" in value:
        timeline = value.get("_timeline")
        if not isinstance(timeline, dict):
            return value
        slots = {}
        for period, slot in timeline.items():
            if not isinstance(slot, dict):
                continue
            converted = _convert_choice(slot, ids, unknown)
            if converted is not None:
                slots[period] = converted
        rest = {k: v for k, v in value.items() if k != "_timeline"}
        if not slots:
            return rest or None
        return {**rest, "_timeline": slots}
    return _convert_choice(value, ids, unknown)


def _convert_nuance_map(raw: Any, ids: dict[str, str], unknown: set[str]) -> dict:
    result = {}
    if not isinstance(raw, dict):
        return result
    for uid, label in raw.items():
        if not isinstance(label, str):
            continue
        option_id = ids.get(label)
        if option_id is None:
            unknown.add(label)
        else:
            result[uid] = option_id
    return result


def convert_nuance_value(value: Any, ids: dict[str, str], unknown: set[str]) -> Any:
    """A stored relation value whose nuances hold ids. Links are untouched."""
    if not isinstance(value, dict):
        return value
    result = dict(value)
    if "nuances" in result:
        converted = _convert_nuance_map(result["nuances"], ids, unknown)
        if converted:
            result["nuances"] = converted
        else:
            del result["nuances"]
    if "nuancePool" in result:
        pool = {}
        if isinstance(result["nuancePool"], dict):
            for uid, ranges in result["nuancePool"].items():
                converted = _convert_nuance_map(ranges, ids, unknown)
                if converted:
                    pool[uid] = converted
        if pool:
            result["nuancePool"] = pool
        else:
            del result["nuancePool"]
    if isinstance(result.get("_timeline"), dict):
        timeline = {}
        for period, slot in result["_timeline"].items():
            if isinstance(slot, dict) and "nuances" in slot:
                slot = dict(slot)
                converted = _convert_nuance_map(slot["nuances"], ids, unknown)
                if converted:
                    slot["nuances"] = converted
                else:
                    del slot["nuances"]
            timeline[period] = slot
        result["_timeline"] = timeline
    return result


def convert_filter_groups(
    groups: Any,
    select_ids: dict[str, dict[str, str]],
    where: str,
    report: Report,
) -> tuple[Any, bool]:
    """
    Filter groups whose conditions on select properties compare against ids.

    *select_ids* maps a select property id to its label-to-id lookup. A
    condition testing a label no option carries is removed, and a group left
    without conditions goes with it. Returns the groups and whether anything
    changed.
    """
    if not isinstance(groups, list):
        return groups, False
    result = []
    changed = False
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("filters"), list):
            result.append(group)
            continue
        filters = []
        for condition in group["filters"]:
            ids = select_ids.get(condition.get("schemaId")) if isinstance(condition, dict) else None
            value = condition.get("value") if isinstance(condition, dict) else None
            if ids is None or not isinstance(value, str) or value == "":
                filters.append(condition)
                continue
            option_id = ids.get(value)
            if option_id is None:
                report.drop(where, f"condition on property {condition['schemaId']} testing {value!r}")
                changed = True
                continue
            if option_id != value:
                condition = {**condition, "value": option_id}
                changed = True
            filters.append(condition)
        if filters:
            result.append({**group, "filters": filters})
        elif group["filters"]:
            changed = True
        else:
            result.append(group)
    return result, changed


def _action_schema_id(action: dict) -> Optional[str]:
    endpoint = action.get("endpoint")
    if not isinstance(endpoint, str):
        return None
    tail = endpoint.rstrip("/").rsplit("/", 1)[-1]
    try:
        return str(uuid.UUID(tail))
    except ValueError:
        return None


def convert_actions(
    actions: Any,
    select_ids: dict[str, dict[str, str]],
    where: str,
    report: Report,
) -> tuple[Any, bool]:
    """Automation actions whose select conditions and written values use ids."""
    if not isinstance(actions, list):
        return actions, False
    result = []
    changed = False
    for action in actions:
        if not isinstance(action, dict):
            result.append(action)
            continue
        action = dict(action)
        spec = action.get("filter")
        if isinstance(spec, dict):
            groups, groups_changed = convert_filter_groups(
                spec.get("groups"), select_ids, where, report,
            )
            if groups_changed:
                action["filter"] = {**spec, "groups": groups}
                changed = True
        target = _action_schema_id(action)
        body = action.get("body")
        if target in select_ids and isinstance(body, dict) and isinstance(body.get("value"), dict):
            unknown: set[str] = set()
            value = convert_select_value(body["value"], select_ids[target], unknown)
            for label in sorted(unknown):
                report.drop(where, f"action value {label!r} on property {target}")
            if value != body["value"]:
                action["body"] = {**body, "value": value}
                changed = True
        result.append(action)
    return result, changed


# ─── Database access ──────────────────────────────────────────────────────────


def _load(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _mirror_of(
    schema_id: str, config: dict, schemas: dict[str, dict],
) -> Optional[str]:
    """The other side of a bilateral relation, found as the router finds it."""
    if config.get("direction") != "bilateral":
        return None
    target = config.get("target_database_id")
    name = config.get("mirror_property_name")
    for other_id, other in schemas.items():
        if (
            other_id != schema_id
            and other["type"] == "relation"
            and str(other["database_id"]) == str(target)
            and other["name"] == name
        ):
            return other_id
    return None


def migrate(connection: sa.engine.Connection) -> Report:
    """Give every option an id and store ids wherever labels were stored."""
    report = Report()
    rows = connection.execute(sa.text(
        "SELECT id, database_id, name, type, config FROM property_schemas "
        "WHERE type IN ('select', 'relation')"
    )).all()
    schemas = {
        str(r.id): {
            "database_id": r.database_id, "name": r.name, "type": r.type,
            "config": dict(_load(r.config) or {}),
        }
        for r in rows
    }

    update_config = sa.text(
        "UPDATE property_schemas SET config = CAST(:config AS jsonb) WHERE id = :id"
    )
    select_ids: dict[str, dict[str, str]] = {}
    nuance_ids: dict[str, dict[str, str]] = {}
    nuance_options: dict[str, list[dict]] = {}

    # Configs first, so both sides of a bilateral relation can share ids.
    for schema_id, schema in schemas.items():
        config = schema["config"]
        if schema["type"] == "select":
            config["options"] = assign_ids(config.get("options"))
            select_ids[schema_id] = lookup(config["options"])
            connection.execute(update_config, {"id": schema_id, "config": json.dumps(config)})
            continue
        nuance = config.get("nuance")
        if not isinstance(nuance, dict) or "options" not in nuance:
            continue
        mirror = _mirror_of(schema_id, config, schemas)
        known = {o["label"]: o["id"] for o in nuance_options.get(mirror, [])} if mirror else {}
        options = assign_ids(nuance.get("options"), known)
        nuance_options[schema_id] = options
        nuance_ids[schema_id] = lookup(options)
        config["nuance"] = {**nuance, "options": options}
        connection.execute(update_config, {"id": schema_id, "config": json.dumps(config)})

    update_value = sa.text(
        "UPDATE property_values SET value = CAST(:value AS jsonb) WHERE id = :id"
    )
    for schema_id, ids in [*select_ids.items(), *nuance_ids.items()]:
        is_select = schema_id in select_ids
        convert = convert_select_value if is_select else convert_nuance_value
        unknown: set[str] = set()
        removed = 0
        for pv in connection.execute(sa.text(
            "SELECT id, value FROM property_values "
            "WHERE property_schema_id = :schema AND value IS NOT NULL"
        ), {"schema": schema_id}):
            before = _load(pv.value)
            found: set[str] = set()
            after = convert(before, ids, found)
            if found:
                removed += 1
                unknown |= found
            if after != before:
                connection.execute(update_value, {"id": pv.id, "value": json.dumps(after)})
        if unknown:
            kind = "select" if is_select else "nuance"
            report.drop(
                f"property {schema_id}",
                f"{kind} labels without an option, in {removed} value(s): {sorted(unknown)!r}",
            )

    if select_ids:
        for view in connection.execute(sa.text(
            "SELECT id, filter_groups FROM database_views"
        )):
            groups, changed = convert_filter_groups(
                _load(view.filter_groups), select_ids, f"view {view.id}", report,
            )
            if changed:
                connection.execute(sa.text(
                    "UPDATE database_views SET filter_groups = CAST(:groups AS jsonb) WHERE id = :id"
                ), {"id": view.id, "groups": json.dumps(groups)})

        for automation in connection.execute(sa.text("SELECT id, actions FROM automations")):
            actions, changed = convert_actions(
                _load(automation.actions), select_ids, f"automation {automation.id}", report,
            )
            if changed:
                connection.execute(sa.text(
                    "UPDATE automations SET actions = CAST(:actions AS json) WHERE id = :id"
                ), {"id": automation.id, "actions": json.dumps(actions)})

    return report


def upgrade() -> None:
    report = migrate(op.get_bind())
    for line in report.dropped:
        log.warning("option migration removed %s", line)
    if report.dropped:
        log.warning("option migration removed %d item(s) in total", len(report.dropped))


def downgrade() -> None:
    raise NotImplementedError(
        "Revision t0w2v1r30020 migrates data one way; restore a backup to go back."
    )
