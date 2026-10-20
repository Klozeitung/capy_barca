"""
System-managed properties of database entries.

``id``, ``created_*`` and ``last_edited_*`` are written by the backend: once
when an entry is created (:func:`populate_readonly_properties`) and, for
``last_edited_*``, on every value write (:func:`refresh_last_edited`).
Together with ``formula`` and ``rollup``, which ``app.blocks.computed``
evaluates, they cannot be written through the value endpoint
(:data:`READONLY_TYPES`).
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.blocks import repository as repo


# Property types whose values are managed by the backend, not via the
# regular upsert endpoint.  An attempt to upsert these via the API is
# rejected with 422.  They are populated automatically on entry creation
# and (for last_edited_*, formula, rollup) on every value upsert.
# sub_item is user-writable (direct writes are accepted and trigger bilateral
# sync via sync_sub_item); it is also maintained automatically as a mirror
# whenever parent_item is written.
READONLY_TYPES: frozenset[str] = frozenset(
    {
        "id",
        "created_by",
        "created_time",
        "last_edited_by",
        "last_edited_time",
        "formula",
        "rollup",
    }
)


def populate_readonly_properties(
    db: Session,
    database_id: uuid.UUID,
    entry_id: uuid.UUID,
    created_at: datetime,
    user_id: uuid.UUID | None = None,
) -> None:
    """
    Write initial values for all readonly property schemas in *database_id*
    that belong to the freshly created entry *entry_id*.

    Called once immediately after entry creation, inside the same transaction.

    Readonly types handled
    ----------------------
    id               – next sequential integer within the schema (config.next_id).
                       The config is patched (+1) in the same transaction.
    created_by       – current application username.
    created_time     – the Block.created_at timestamp (ISO-8601, UTC).
    last_edited_by   – same as created_by at creation time.
    last_edited_time – same as created_time at creation time.
    """
    schemas = repo.list_schemas(db, database_id)
    now_iso = created_at.astimezone(timezone.utc).isoformat()

    for schema in schemas:
        if schema.type == "id":
            config = dict(schema.config or {})
            next_id: int = int(config.get("next_id", 1))
            repo.upsert_value(
                db,
                page_id=entry_id,
                schema_id=schema.id,
                value={"id_value": next_id},
            )
            config["next_id"] = next_id + 1
            repo.update_schema(db, schema, config=config)

        elif schema.type == "created_by":
            repo.upsert_value(
                db,
                page_id=entry_id,
                schema_id=schema.id,
                value={"user_id": str(user_id)} if user_id else None,
            )

        elif schema.type == "created_time":
            repo.upsert_value(
                db,
                page_id=entry_id,
                schema_id=schema.id,
                value={"datetime": now_iso},
            )

        elif schema.type == "last_edited_by":
            repo.upsert_value(
                db,
                page_id=entry_id,
                schema_id=schema.id,
                value={"user_id": str(user_id)} if user_id else None,
            )

        elif schema.type == "last_edited_time":
            repo.upsert_value(
                db,
                page_id=entry_id,
                schema_id=schema.id,
                value={"datetime": now_iso},
            )


def refresh_last_edited(
    db: Session,
    database_id: uuid.UUID,
    entry_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> set[uuid.UUID]:
    """
    Update ``last_edited_by`` and ``last_edited_time`` schemas whenever a
    regular value upsert occurs on *entry_id*.

    Returns the IDs of the schemas written, so the caller counts them among
    the changed properties when it brings computed values up to date. Empty
    when neither schema type exists in the database.
    """
    schemas = repo.list_schemas(db, database_id)
    now_iso = datetime.now(timezone.utc).isoformat()
    written: set[uuid.UUID] = set()

    for schema in schemas:
        if schema.type == "last_edited_by":
            repo.upsert_value(
                db,
                page_id=entry_id,
                schema_id=schema.id,
                value={"user_id": str(user_id)} if user_id else None,
            )
            written.add(schema.id)
        elif schema.type == "last_edited_time":
            repo.upsert_value(
                db,
                page_id=entry_id,
                schema_id=schema.id,
                value={"datetime": now_iso},
            )
            written.add(schema.id)
    return written
