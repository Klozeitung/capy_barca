"""
Access to databases for the database API.

Every handler of ``app.blocks.database_router`` and
``app.blocks.database_entries_router`` resolves its database through
:func:`get_database_or_raise`, which takes the calling user and runs the block
permission check. Reads answer 404 when the caller may not access the
database, so the response does not confirm that it exists; changes answer 403,
matching the block router and the view router. The entry listings that
relation pickers call answer with an empty result instead (``Access.SOFT``).

Writing a relation property also requires access to its target database (see
:func:`require_relation_targets_access`), because a bilateral relation writes
its mirror property and back-references there.
"""
import uuid
from enum import Enum
from typing import Any, Iterable, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.blocks import repository as repo
from app.blocks.models import DatabaseSettings
from app.permissions import repository as perm_repo
from app.permissions.model import READ, WRITE
from app.session.deps import NO_ACCESS_DETAIL, READ_ONLY_DETAIL
from app.users.model import User


class Access(Enum):
    """
    Which access level a handler needs, and how it answers a caller without it.

    READ  – read access; 404 otherwise, the same answer as for a database that
            does not exist, so a read does not confirm that a database the
            caller cannot see exists.
    WRITE – write access; 403 otherwise, matching the block router and the
            view router. A caller with read access only gets the read-only
            detail, so the client can tell the two refusals apart.
    SOFT  – read access; no exception otherwise, the lookup returns ``None``
            and the handler answers with an empty result. Used by the entry
            listings that relation pickers call for arbitrary target databases.
    """

    READ = "read"
    WRITE = "write"
    SOFT = "soft"


def get_database_or_raise(
    db: Session,
    database_id: uuid.UUID,
    user: User,
    access: Access,
):
    """
    Return the database block if *user* may access it, or raise.

    Every endpoint of the database routers resolves its database through this function,
    so the permission check cannot be left out of a handler: the user is a
    required argument. The access check runs before the type check, so a
    caller without access never learns whether the block is a database.

    *access* selects both the level the caller needs and how a refusal is
    answered (see :class:`Access`).

    Returns
    -------
    Block or None
        ``None`` only with ``Access.SOFT`` when the caller may not access
        the database.

    Raises
    ------
    HTTPException(404)
        If no block with *database_id* exists, or with ``Access.READ`` when
        the caller may not access it.
    HTTPException(403)
        With ``Access.WRITE`` when the caller may not change the database.
    HTTPException(409)
        If the block exists but is not of type ``'database'``.
    """
    block = repo.get_block(db, database_id)
    if block is None:
        raise HTTPException(
            status_code=404, detail=f"Database {database_id} not found"
        )
    granted = perm_repo.access_level(db, database_id, user)
    required = WRITE if access is Access.WRITE else READ
    if not perm_repo.satisfies(granted, required):
        if access is Access.SOFT:
            return None
        if access is Access.WRITE:
            detail = NO_ACCESS_DETAIL if granted is None else READ_ONLY_DETAIL
            raise HTTPException(status_code=403, detail=detail)
        raise HTTPException(
            status_code=404, detail=f"Database {database_id} not found"
        )
    if block.type != "database":
        raise HTTPException(
            status_code=409,
            detail=f"Block {database_id} is not a database (type: '{block.type}')",
        )
    return block


def may_manage_database(database: Any, user: User) -> bool:
    """Whether *user* may change the settings of *database*: its owner or an admin."""
    return user.role == "admin" or (
        database.owner_id is not None and database.owner_id == user.id
    )


def default_entry_icon(db: Session, database_id: uuid.UUID) -> Optional[str]:
    """The icon new entries of *database_id* start with, or None."""
    settings = db.get(DatabaseSettings, database_id)
    return settings.default_entry_icon if settings is not None else None


def relation_target_id(config: Optional[dict]) -> Optional[uuid.UUID]:
    """Return the ``target_database_id`` of a relation config, or None if absent or malformed."""
    raw = (config or {}).get("target_database_id")
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None


def require_relation_targets_access(
    db: Session,
    database_id: uuid.UUID,
    configs: Iterable[Optional[dict]],
    user: User,
) -> None:
    """
    Refuse with 403 unless *user* may change every relation target in *configs*.

    Writing a relation property reaches into its target database: a bilateral
    relation creates, renames and reconfigures its mirror property there and
    writes back-references into the target's entries, and a relation value
    names entries of the target. Access to the database holding the property
    is therefore not enough; the caller must also have write access to the
    target.

    Targets equal to *database_id* (self-relations) are covered by the access
    check on the database itself and are skipped. A target that does not
    exist resolves to unrestricted in the permission layer and passes, which
    keeps a dangling pointer from blocking edits to its own property.
    """
    targets = {
        target
        for target in (relation_target_id(config) for config in configs)
        if target is not None and target != database_id
    }
    if not targets:
        return
    if perm_repo.filter_accessible(db, targets, user, level=WRITE) != targets:
        raise HTTPException(status_code=403, detail="Not authorized for the relation target")


def copied_relation_configs(pvs: Iterable, schema_map: dict) -> list[Optional[dict]]:
    """
    Return the configs of the relation properties among *pvs* that carry a
    value, i.e. the relations a duplicate or a template application writes.
    """
    configs: list[Optional[dict]] = []
    for pv in pvs:
        schema = schema_map.get(pv.property_schema_id)
        if schema is not None and schema.type == "relation" and pv.value is not None:
            configs.append(schema.config)
    return configs
