"""kinship roles

Revision ID: c5f7e6g80035
Revises: b4e6d5f70034
Create Date: 2026-11-06

Marks the existing properties of the family set (see ``app.blocks.kinship``).
A property whose name is reserved for a role, in German or English and
ignoring case and surrounding spaces, and whose structure fits that role gets
``config.kinRole``; a database holds each role at most once, the first
property by position wins. A select property marked ``sex`` points
``config.kinFemaleOptionId`` at its option labelled female in either
language, when it has one.

The relations among the marked properties offer the system nuances from now
on, which are not stored on the property. Nuance options such a relation
stored itself are dropped, together with the nuances its values carried.

Properties with a reserved name that do not fit their role stay unmarked;
their values are not touched.

The rules are copied here on purpose: a migration must keep doing what it did
when it was written, whatever the application code later becomes.

Downgrade
---------
Removes the markers. Dropped nuance options do not come back.
"""
import json
from typing import Any, Optional, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c5f7e6g80035"
down_revision: Union[str, None] = "b4e6d5f70034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROLE_KEY = "kinRole"
FEMALE_OPTION_KEY = "kinFemaleOptionId"

RESERVED_NAMES = {
    "parents": ("Eltern", "Parents"),
    "children": ("Kinder", "Children"),
    "partners": ("Partner", "Partners"),
    "siblings": ("Geschwister", "Siblings"),
    "half_siblings": ("Halbgeschwister", "Half-siblings"),
    "birth": ("Geburtsdatum", "Date of birth"),
    "death": ("Todeszeitpunkt", "Time of death"),
    "sex": ("Geschlecht (Biologisch)", "Sex (biological)"),
}
FEMALE_LABELS = ("weiblich", "female")
MIRRORED = {"parents": "children", "children": "parents"}
SELF_ROLES = ("partners", "siblings", "half_siblings")
RELATION_ROLES = ("parents", "children", *SELF_ROLES)
DATE_TYPES = ("date", "rollup", "formula")


def _fold(name: Any) -> str:
    return name.strip().casefold() if isinstance(name, str) else ""


_NAME_ROLES = {_fold(n): role for role, names in RESERVED_NAMES.items() for n in names}


def role_for_name(name: Any) -> Optional[str]:
    return _NAME_ROLES.get(_fold(name))


def fits(role: str, type_: str, config: dict, database_id: str) -> bool:
    if role in ("birth", "death"):
        return type_ in DATE_TYPES
    if role == "sex":
        return type_ == "select"
    if type_ != "relation" or str(config.get("target_database_id") or "") != database_id:
        return False
    if role in SELF_ROLES:
        return config.get("direction") == "bilateral_self"
    return (
        config.get("direction") == "bilateral"
        and role_for_name(config.get("mirror_property_name")) == MIRRORED[role]
    )


def female_option(config: dict) -> Optional[str]:
    for option in config.get("options") or []:
        if isinstance(option, dict) and _fold(option.get("label")) in FEMALE_LABELS:
            return option.get("id") if isinstance(option.get("id"), str) else None
    return None


def assign_roles(schemas: list[dict]) -> dict[str, str]:
    """
    Schema id to role for *schemas* (dicts with ``id``, ``database_id``,
    ``name``, ``type``, ``config``, ``position``): per database each role
    goes to the first fitting property by position.
    """
    result: dict[str, str] = {}
    taken: set[tuple[str, str]] = set()
    for schema in sorted(schemas, key=lambda s: (s["database_id"], s["position"], s["id"])):
        role = role_for_name(schema["name"])
        if role is None or (schema["database_id"], role) in taken:
            continue
        if fits(role, schema["type"], schema["config"], schema["database_id"]):
            result[schema["id"]] = role
            taken.add((schema["database_id"], role))
    return result


def _strip_map(nuances: Any, removed: set[str]) -> Any:
    if not isinstance(nuances, dict):
        return nuances
    return {uid: n for uid, n in nuances.items() if n not in removed}


def strip_nuances(value: Any, removed: set[str]) -> Any:
    """A relation value without the nuances whose id is in *removed*."""
    if not isinstance(value, dict) or not removed:
        return value
    result = dict(value)
    if "nuances" in result:
        kept = _strip_map(result["nuances"], removed)
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
                kept = _strip_map(slot["nuances"], removed)
                if kept:
                    slot["nuances"] = kept
                else:
                    del slot["nuances"]
            timeline[period] = slot
        result["_timeline"] = timeline
    return result


def marked_config(config: dict, role: str) -> tuple[dict, set[str]]:
    """
    *config* with the markers of *role*, and the nuance option ids it gives
    up for the system nuances.
    """
    result = {**config, ROLE_KEY: role}
    removed: set[str] = set()
    if role == "sex":
        female = female_option(config)
        if female:
            result[FEMALE_OPTION_KEY] = female
    if role in RELATION_ROLES and isinstance(config.get("nuance"), dict):
        nuance = config["nuance"]
        removed = {
            o["id"] for o in nuance.get("options") or []
            if isinstance(o, dict) and isinstance(o.get("id"), str)
        }
        if "options" in nuance:
            result["nuance"] = {**nuance, "options": []}
    return result, removed


def _load(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def migrate(connection: sa.engine.Connection) -> dict[str, str]:
    """Mark the family set properties. Returns schema id to role."""
    rows = connection.execute(sa.text(
        "SELECT id, database_id, name, type, config, position FROM property_schemas"
    )).all()
    schemas = [
        {
            "id": str(r.id), "database_id": str(r.database_id), "name": r.name,
            "type": r.type, "config": dict(_load(r.config) or {}), "position": r.position,
        }
        for r in rows
        if role_for_name(r.name) is not None
    ]
    roles = assign_roles(schemas)
    by_id = {s["id"]: s for s in schemas}
    for schema_id, role in roles.items():
        config, removed = marked_config(by_id[schema_id]["config"], role)
        connection.execute(sa.text(
            "UPDATE property_schemas SET config = CAST(:config AS jsonb) WHERE id = :id"
        ), {"id": schema_id, "config": json.dumps(config)})
        if not removed:
            continue
        for pv in connection.execute(sa.text(
            "SELECT id, value FROM property_values "
            "WHERE property_schema_id = :schema AND value IS NOT NULL"
        ), {"schema": schema_id}).all():
            before = _load(pv.value)
            after = strip_nuances(before, removed)
            if after != before:
                connection.execute(sa.text(
                    "UPDATE property_values SET value = CAST(:value AS jsonb) WHERE id = :id"
                ), {"id": pv.id, "value": json.dumps(after)})
    return roles


def upgrade() -> None:
    migrate(op.get_bind())


def downgrade() -> None:
    op.execute(
        "UPDATE property_schemas "
        f"SET config = config - '{ROLE_KEY}' - '{FEMALE_OPTION_KEY}' "
        f"WHERE config ? '{ROLE_KEY}' OR config ? '{FEMALE_OPTION_KEY}'"
    )
