"""
Relation resolution.

Relation values store only the ids of the linked entries. Whoever displays an
entry also needs the titles behind those ids, and for keyed relations the key
values the linked entries are ordered by. This module turns ids into that
information, for one relation cell or for a whole page of entries at once.

There is one implementation, used by every path that answers the question:

* ``POST /entries/resolve-titles`` resolves ids of one target database on
  request, for the cases where the frontend learns of a new id on its own
  (creating and linking an entry, the timeline editor).
* View pages and single entries carry the resolution of every relation they
  contain, so a client never has to ask for titles separately.

Both apply the same rules: only active, non-template entries that are direct
children of the relation's target database resolve, and a target database
the caller may not read resolves nothing. An id that does not resolve is
simply absent from the result; a client treats every id of an entry it
received resolved but missing from the map as gone.

Cost
----
``resolve_relations`` issues a fixed number of queries regardless of how many
entries, relation properties or target databases are involved: one for the
permission walk of all target databases, one for the linked blocks, and, when
keyed relations are present, one for the key properties and one for their
values.
"""
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.blocks import repository as repo
from app.blocks.models import Block, PropertySchema, PropertyValue
from app.permissions import repository as perm_repo
from app.users.model import User

# Property types whose values hold links to entries.
RELATION_TYPES: frozenset[str] = frozenset({"relation", "parent_item", "sub_item"})

# Entry templates live among a database's children but are never link targets.
_EXCLUDED_TYPES: frozenset[str] = frozenset({"entry_template"})


@dataclass(frozen=True)
class EntryTitle:
    """What a relation chip needs to render one linked entry."""

    id: uuid.UUID
    title: Optional[str]
    database_id: uuid.UUID
    value: Optional[dict] = None

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "title": self.title,
            "database_id": str(self.database_id),
        }


@dataclass
class Resolution:
    """
    Resolved relations of a set of entries.

    ``titles`` maps a linked entry id to its descriptor. ``key_values`` maps a
    key property id to the values its entries hold for it, keyed by entry id;
    an entry without a stored value is absent.
    """

    titles: dict[str, EntryTitle] = field(default_factory=dict)
    key_values: dict[str, dict[str, Any]] = field(default_factory=dict)

    def as_payload(self) -> dict:
        return {
            "relations": {key: title.as_dict() for key, title in self.titles.items()},
            "key_values": self.key_values,
        }


# ─── Single target database ──────────────────────────────────────────────────


def resolve_titles(
    db: Session,
    database_id: uuid.UUID,
    ids: Iterable[uuid.UUID],
    value_schema_id: Optional[uuid.UUID] = None,
) -> list[EntryTitle]:
    """
    Resolve entry ids of one target database to titles.

    No permission check: the caller decides whether the database may be read.
    With *value_schema_id*, each descriptor also carries the entry's stored
    value for that property, provided the property belongs to *database_id*;
    otherwise ``value`` stays ``None``.
    """
    wanted = list(dict.fromkeys(ids))
    blocks = repo.list_blocks_by_ids(
        db, wanted, parent_id=database_id, state="active", exclude_types=_EXCLUDED_TYPES,
    )
    if not blocks:
        return []

    values: dict[uuid.UUID, Optional[dict]] = {}
    if value_schema_id is not None:
        value_schema = repo.get_schema(db, value_schema_id)
        if value_schema is not None and value_schema.database_id == database_id:
            stmt = select(PropertyValue.page_id, PropertyValue.value).where(
                PropertyValue.page_id.in_([b.id for b in blocks]),
                PropertyValue.property_schema_id == value_schema_id,
            )
            values = {page_id: value for page_id, value in db.execute(stmt)}

    return [
        EntryTitle(
            id=block.id,
            title=(block.content or {}).get("title"),
            database_id=database_id,
            value=values.get(block.id),
        )
        for block in blocks
    ]


# ─── Whole pages of entries ──────────────────────────────────────────────────


def linked_ids(value: Any) -> list[str]:
    """
    Every entry id a relation value links to.

    A plain value carries ``related_ids``. A value with a timeline carries one
    list per period under ``_timeline``; all of them count, since a cell may
    display any period and the timeline editor shows them all.
    """
    if not isinstance(value, dict):
        return []
    if "_timeline" in value:
        timeline = value.get("_timeline")
        if not isinstance(timeline, dict):
            return []
        seen: dict[str, None] = {}
        for slot in timeline.values():
            if isinstance(slot, dict):
                for entry_id in slot.get("related_ids") or []:
                    if isinstance(entry_id, str):
                        seen.setdefault(entry_id, None)
        return list(seen)
    ids = value.get("related_ids") or []
    return [entry_id for entry_id in ids if isinstance(entry_id, str)]


def _target_database(schema: PropertySchema) -> uuid.UUID:
    """The database a relation property links into."""
    raw = (schema.config or {}).get("target_database_id")
    if raw:
        try:
            return uuid.UUID(str(raw))
        except ValueError:
            pass
    return schema.database_id


def _key_property_id(schema: PropertySchema) -> Optional[uuid.UUID]:
    """The key property of a keyed relation, or None when keying is off."""
    keying = (schema.config or {}).get("keying")
    if not isinstance(keying, dict) or keying.get("enabled") is not True:
        return None
    raw = keying.get("key_property_id")
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None


def resolve_relations(
    db: Session,
    schemas: Iterable[PropertySchema],
    values_by_entry: Iterable[dict[str, Any]],
    user: User,
) -> Resolution:
    """
    Resolve every relation the given entries carry.

    *schemas* are the property schemas of the entries' database.
    *values_by_entry* yields one ``{schema_id: value}`` mapping per entry, in
    the shape the entry payloads use.

    Linked entries in a database *user* may not read do not resolve, exactly
    as ``resolve-titles`` answers an empty list for such a database.
    """
    relation_schemas = [s for s in schemas if s.type in RELATION_TYPES]
    if not relation_schemas:
        return Resolution()

    # Which entry ids are wanted from which target database, and which of them
    # need a key value from which key property.
    wanted: dict[uuid.UUID, set[uuid.UUID]] = {}
    keyed: dict[uuid.UUID, tuple[uuid.UUID, set[uuid.UUID]]] = {}
    for values in values_by_entry:
        for schema in relation_schemas:
            ids = []
            for raw in linked_ids(values.get(str(schema.id))):
                try:
                    ids.append(uuid.UUID(raw))
                except ValueError:
                    continue
            if not ids:
                continue
            target = _target_database(schema)
            wanted.setdefault(target, set()).update(ids)
            key_property = _key_property_id(schema)
            if key_property is not None:
                keyed.setdefault(key_property, (target, set()))[1].update(ids)

    if not wanted:
        return Resolution()

    readable = perm_repo.filter_accessible(db, wanted.keys(), user)
    target_of: dict[uuid.UUID, set[uuid.UUID]] = {}
    for target, ids in wanted.items():
        if target not in readable:
            continue
        for entry_id in ids:
            target_of.setdefault(entry_id, set()).add(target)
    if not target_of:
        return Resolution()

    # One query for every linked block of every target database. A block is
    # accepted for a relation only if it really is a child of the database
    # that relation points into.
    stmt = select(Block).where(
        Block.id.in_(target_of.keys()),
        Block.state == "active",
        Block.type.not_in(_EXCLUDED_TYPES),
    )
    resolution = Resolution()
    for block in db.scalars(stmt):
        if block.parent_id in target_of.get(block.id, set()):
            resolution.titles[str(block.id)] = EntryTitle(
                id=block.id,
                title=(block.content or {}).get("title"),
                database_id=block.parent_id,
            )

    _resolve_key_values(db, keyed, readable, resolution)
    return resolution


def _resolve_key_values(
    db: Session,
    keyed: dict[uuid.UUID, tuple[uuid.UUID, set[uuid.UUID]]],
    readable: set[uuid.UUID],
    resolution: Resolution,
) -> None:
    """Fill ``resolution.key_values`` for the keyed relations among the entries."""
    if not keyed:
        return

    # A key property counts only if it belongs to the relation's target
    # database: a pointer left dangling by a concurrent change degrades to no
    # key values instead of reading another database's data.
    stmt = select(PropertySchema.id, PropertySchema.database_id).where(
        PropertySchema.id.in_(keyed.keys())
    )
    valid: dict[uuid.UUID, set[uuid.UUID]] = {}
    for key_property, database_id in db.execute(stmt):
        target, ids = keyed[key_property]
        if database_id != target or target not in readable:
            continue
        resolved = {
            uuid.UUID(entry_id)
            for entry_id, title in resolution.titles.items()
            if title.database_id == target
        }
        ids = ids & resolved
        if ids:
            valid[key_property] = ids
    if not valid:
        return

    all_ids = set().union(*valid.values())
    stmt = select(
        PropertyValue.property_schema_id, PropertyValue.page_id, PropertyValue.value
    ).where(
        PropertyValue.property_schema_id.in_(valid.keys()),
        PropertyValue.page_id.in_(all_ids),
    )
    for key_property in valid:
        resolution.key_values[str(key_property)] = {}
    for key_property, page_id, value in db.execute(stmt):
        if page_id in valid[key_property]:
            resolution.key_values[str(key_property)][str(page_id)] = value
