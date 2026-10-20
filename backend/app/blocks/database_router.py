"""
Database router.

HTTP interface for databases and their property schemas: the database list,
the settings of a database, property schema CRUD, option and key-reference
pre-flights, and formula validation. Entries, their values and entry
templates are served by ``app.blocks.database_entries_router`` under the same
prefix.

All endpoints require a valid session cookie. The steps of creating and
updating a schema live in ``app.blocks.schema_service``; this module checks
access, runs them in one transaction, commits and broadcasts.

Access
------
Every endpoint resolves its database through
``app.blocks.database_access.get_database_or_raise``, which takes the calling
user and runs the block permission check. Reads answer 404 when the caller may
not access the database, so the response does not confirm that it exists;
changes answer 403, matching the block router and the view router.
``GET /api/databases`` lists only the databases the caller may access.

Writing a relation property also requires access to its target database (see
``require_relation_targets_access``), because a bilateral relation writes its
mirror property there.

Convention
----------
* Every handler is a synchronous ``def``. FastAPI runs those in a worker
  thread, so their blocking database work — including the formula and rollup
  cascades of a schema change — never stalls the event loop that serves all
  other requests and the open WebSocket connections.
* Mutation handlers broadcast through ``app.blocks.database_events`` after a
  successful ``db.commit()``. Pure schema edits broadcast nothing; a schema
  update that rewrote entry values announces it for every database affected.

Relation properties
-------------------
When a ``relation``-type schema has ``config.direction == "bilateral"``:

* **At schema creation** the mirror schema is created immediately in the
  target database so it is visible before any entry is linked.
* **At schema update**: if ``mirror_property_name`` changes the existing
  mirror schema is renamed (data preserved); if the source schema's own name
  changes the mirror's back-pointer config is updated accordingly; the
  timeline flag and the nuance configuration are copied to the mirror.

With ``config.direction == "bilateral_self"`` no separate mirror schema is
created — the schema mirrors itself.

A relation property may nominate a *key property* of its target database, at
``config.keying``, which sorts the linked entries and is rendered beside each
of them. Keying is a read-side pointer, not edge data: it never participates
in bilateral synchronisation and both sides configure it independently.
Because the pointer lives inside a JSON blob it cannot be a foreign key.
Deleting the referenced property, retyping it out of the supported set, or
deleting its database resets every referring relation to vanilla in the same
transaction (see ``app.blocks.relation_sync.clear_keying_for_property``).

Sub-item hierarchy
------------------
Every database receives a linked ``parent_item`` / ``sub_item`` schema pair via
``seed_readonly_schemas`` (and when a view first displays it). Writing either
keeps the other in sync; see ``app.blocks.relation_sync``.

Computed properties (formula / rollup)
---------------------------------------
Cycle detection runs on every formula / rollup schema create or update,
using Kahn's topological-sort algorithm. A detected cycle returns 422. When
a computed config changes, every entry of the database is re-evaluated in
the same transaction.

Formulas are stored with property ids and exchanged with names: every schema
this router returns shows a formula's ``prop()`` references as names, and a
formula sent with names is stored with ids. Renaming a property therefore
leaves formulas intact; deleting one turns the references to it back into its
name (see ``schema_service``, "Formula references"). ``GET /formula-syntax``
lists the functions and syntax a formula can use, for the editor's reference.

Database settings
-----------------
Settings of the whole database, as opposed to one of its views, live in
``database_settings`` (``GET``/``PUT /{database_id}/settings``). Reading them
needs read access; changing them is reserved for the database's owner and
admins. ``default_entry_icon`` is given to every entry ``create_entry`` makes;
applying a template with an icon of its own replaces it, and duplicating an
entry keeps the original's icon.
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.blocks import options as opts
from app.blocks import repository as repo
from app.blocks import schema_service
from app.blocks.computed import SchemaLike, recompute_database_and_readers
from app.blocks.database_access import (
    Access,
    default_entry_icon,
    get_database_or_raise,
    may_manage_database,
    require_relation_targets_access,
)
from app.blocks.database_events import emit_entries_updated_for, emit_schema_updated
from app.blocks.database_payloads import (
    DatabaseListResponse,
    DatabaseSettingsResponse,
    DatabaseSettingsUpdate,
    FormulaSyntaxEntry,
    FormulaSyntaxResponse,
    FormulaValidateRequest,
    FormulaValidateResponse,
    KeyReferenceResponse,
    OptionUsageResponse,
    SchemaCreate,
    SchemaResponse,
    SchemaUpdate,
)
from app.blocks.formula_engine import (
    SYNTAX_CATEGORIES,
    FormulaError,
    syntax_catalogue,
    validate_syntax,
)
from app.blocks.models import DatabaseSettings
from app.blocks.relation_sync import (
    assert_relation_cross_side_lock,
    clear_keying_for_property,
    validate_relation_mode_exclusivity,
)
from app.blocks.router import get_db
from app.permissions import repository as perm_repo
from app.permissions.model import READ
from app.session.deps import get_current_user
from app.users.model import User

database_router = APIRouter(prefix="/api/databases", tags=["databases"])


def _schema_response(schema, schemas: list) -> SchemaResponse:
    """
    *schema* as the API returns it, with a formula's references as names of
    *schemas*, the properties of its database.
    """
    return SchemaResponse(
        id=schema.id,
        database_id=schema.database_id,
        name=schema.name,
        type=schema.type,
        config=schema_service.client_config(schema, schemas),
        position=schema.position,
        group=schema.group,
    )


# ─── Database list endpoint ───────────────────────────────────────────────────


@database_router.get(
    "",
    response_model=list[DatabaseListResponse],
)
def list_databases(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return the active database blocks the caller may access as lightweight
    descriptors (id + title).

    Used by the frontend to populate the target-database picker in the
    relation property settings modal. Databases the caller may not access are
    left out, so the picker neither reveals their titles nor offers a target
    that relation writes would refuse.
    """
    blocks = repo.list_databases(db)
    accessible = perm_repo.filter_accessible(
        db, (b.id for b in blocks), current_user, level=READ,
    )
    blocks = [b for b in blocks if b.id in accessible]
    return [
        DatabaseListResponse(
            id=b.id,
            title=(b.content or {}).get("title"),
        )
        for b in blocks
    ]


# ─── Schema endpoints ─────────────────────────────────────────────────────────


@database_router.get(
    "/{database_id}/schemas",
    response_model=list[SchemaResponse],
)
def list_schemas(
    database_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return all property schemas for a database, ordered by position, formulas
    with their references as names.
    """
    get_database_or_raise(db, database_id, current_user, Access.READ)
    schemas = repo.list_schemas(db, database_id)
    return [_schema_response(s, schemas) for s in schemas]


@database_router.get(
    "/{database_id}/settings",
    response_model=DatabaseSettingsResponse,
)
def get_database_settings(
    database_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return the settings of the whole database.

    Needs read access (404 otherwise). A database without stored settings
    answers with the defaults.
    """
    database = get_database_or_raise(db, database_id, current_user, Access.READ)
    return DatabaseSettingsResponse(
        default_entry_icon=default_entry_icon(db, database_id),
        can_manage=may_manage_database(database, current_user),
    )


@database_router.put(
    "/{database_id}/settings",
    response_model=DatabaseSettingsResponse,
)
def update_database_settings(
    database_id: uuid.UUID,
    payload: DatabaseSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Replace the settings of the whole database.

    Only the owner of the database and admins may: anyone else who can read
    it receives 403, anyone who cannot read it 404. Icon names outside the
    bundled icon sets are rejected by request validation (422).
    """
    database = get_database_or_raise(db, database_id, current_user, Access.READ)
    if not may_manage_database(database, current_user):
        raise HTTPException(
            status_code=403,
            detail="Only the owner of the database or an admin may change its settings",
        )
    settings = db.get(DatabaseSettings, database_id)
    if settings is None:
        settings = DatabaseSettings(database_id=database_id)
        db.add(settings)
    settings.default_entry_icon = payload.default_entry_icon
    db.commit()
    return DatabaseSettingsResponse(
        default_entry_icon=settings.default_entry_icon,
        can_manage=True,
    )


@database_router.post(
    "/{database_id}/schemas",
    response_model=SchemaResponse,
    status_code=201,
)
def create_schema(
    database_id: uuid.UUID,
    payload: SchemaCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Add a new property schema to a database.

    If ``position`` is omitted the schema is appended after the last existing
    one. Returns 409 if a schema with the same name already exists in this
    database. Returns 422 if a formula / rollup config would create a cycle or
    contains a syntax error.

    For bilateral relation schemas, the mirror schema is created eagerly in the
    target database.  A ``database_schema_updated`` WebSocket event is broadcast
    to the target database so open table views can refresh their schema lists
    immediately (and hide the new property automatically).

    A relation property requires access to its target database as well and
    answers 403 otherwise.

    The options of a select property and the nuance options of a relation are
    stored in canonical form, each with an id (see ``app.blocks.options``).

    A formula is stored with its references as property ids. Creating a
    property also binds references other formulas made to its name before it
    existed.
    """
    get_database_or_raise(db, database_id, current_user, Access.WRITE)
    if payload.type == "relation":
        require_relation_targets_access(db, database_id, [payload.config], current_user)
    config = opts.normalize_config(payload.type, payload.config)
    position = (
        payload.position
        if payload.position is not None
        else schema_service.schema_position_after_last(db, database_id)
    )

    if payload.type in schema_service.COMPUTED_TYPES:
        schema_service.check_computed_config(
            db, database_id, uuid.uuid4(), payload.name, payload.type, payload.config,
        )
    if payload.type == "relation":
        validate_relation_mode_exclusivity(payload.config)

    try:
        schema = repo.create_schema(
            db,
            database_id=database_id,
            name=payload.name,
            type=payload.type,
            position=position,
            config=config,
            visible_in_view_id=payload.visible_in_view_id,
        )
        schema.group = payload.group
        schema_service.bind_formula_references(db, database_id)
        mirror_created_in = schema_service.create_eager_mirror(db, schema, payload.config)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Property '{payload.name}' already exists in this database",
        )
    except Exception:
        db.rollback()
        raise

    # Open views of the target database refresh their schema list and hide
    # the new mirror property.
    if mirror_created_in is not None:
        emit_schema_updated(db, mirror_created_in)

    return _schema_response(schema, repo.list_schemas(db, database_id))


@database_router.patch(
    "/{database_id}/schemas/{schema_id}",
    response_model=SchemaResponse,
)
def update_schema(
    database_id: uuid.UUID,
    schema_id: uuid.UUID,
    payload: SchemaUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update name, type, config, or position of a property schema.

    Only supplied (non-null) fields are changed. Returns 409 if a rename
    would collide with an existing schema name in the same database.
    Returns 422 if a formula / rollup config update would create a cycle or
    contains a syntax error, or a relation config combines modes that may not
    be combined.

    Changing the name, type or config of a relation property, or turning a
    property into one, requires access to its current and its new target
    database and answers 403 otherwise, because those changes propagate to
    the mirror property there. Moving it or changing its group stays within
    this database and needs no access to the target.

    Side effects, all in the same transaction
    -----------------------------------------
    * Removed options are purged; relabelled select options and a changed
      computed config, or a rename that rewrote ``prop()`` references,
      re-evaluate the computed properties that show them.
    * A retype out of the keyable types resets every relation keyed on the
      property.
    * A bilateral relation carries the change over to its mirror (see
      ``schema_service.propagate_to_mirror``).
    * Enabling ``hasTimeline`` wraps the existing values into the timeline
      format (see ``schema_service.migrate_values_for_enabled_timeline``).

    Options
    -------
    The options of a select property and the nuance options of a relation
    are stored in canonical form, each with an id; an option sent without id
    keeps the id of the existing option with the same label. An option that
    disappears from the config is removed in the same transaction from every
    entry and template value, from the filters of every view and from every
    automation. When options are renamed or removed, the formulas and rollups
    that show them are recomputed, here and in databases that read this one.
    A nuance option removed on one side of a bilateral relation is removed
    from the mirror's values as well, since both sides share the option set.

    A ``database_entries_updated`` event is broadcast for every database whose
    entry values the update rewrote; pure schema edits broadcast nothing.
    """
    get_database_or_raise(db, database_id, current_user, Access.WRITE)
    schema = repo.get_schema(db, schema_id)
    if schema is None or schema.database_id != database_id:
        raise HTTPException(
            status_code=404,
            detail=f"Schema {schema_id} not found in database {database_id}",
        )

    config_changed = payload.config is not None
    touches_relation = "relation" in (schema.type, payload.type)
    propagates = payload.name is not None or payload.type is not None or config_changed
    if touches_relation and propagates:
        require_relation_targets_access(
            db, database_id, [schema.config, payload.config], current_user,
        )

    old_name = schema.name
    old_config = dict(schema.config or {})
    effective_type = payload.type if payload.type is not None else schema.type
    new_config = (
        opts.normalize_config(effective_type, payload.config, old_config)
        if config_changed
        else None
    )
    effective_config = new_config if new_config is not None else schema.config
    effective_name = payload.name if payload.name is not None else schema.name
    removed_options, options_relabelled = schema_service.option_changes(
        schema.type, effective_type, old_config, new_config,
    )
    computed_config_changed = effective_type in schema_service.COMPUTED_TYPES and config_changed

    # Every gate runs before anything is written, so a rejected update leaves
    # the schema untouched. The mirror for the cross-side lock is resolved
    # from the pre-update config, which is where the existing mirror lives.
    if computed_config_changed:
        schema_service.check_computed_config(
            db, database_id, schema_id, effective_name, effective_type, effective_config,
        )
    if effective_type == "relation" and config_changed:
        validate_relation_mode_exclusivity(effective_config)
        assert_relation_cross_side_lock(db, schema, effective_config)
    retyped_out_of_keyable = schema_service.retyped_out_of_keyable(schema.type, payload.type)

    if touches_relation and config_changed:
        schema_service.lock_for_relation_config(
            db, database_id, schema,
            SchemaLike(id=schema.id, name=effective_name, type=effective_type, config=effective_config),
        )

    # Databases whose entry values the update rewrote.
    migrated: set[uuid.UUID] = set()
    try:
        updated = repo.update_schema(
            db,
            schema,
            name=payload.name,
            type=payload.type,
            config=new_config,
            position=payload.position,
        )
        if payload.group is not None:
            updated.group = payload.group

        if removed_options and schema_service.purge_options(db, updated, removed_options):
            migrated.add(database_id)
        if options_relabelled:
            migrated.update(recompute_database_and_readers(db, database_id))
        if retyped_out_of_keyable:
            clear_keying_for_property(db, schema_id)

        # Formulas store property ids, so a rename leaves them intact. A
        # formula saved with names, and references written by name to what
        # this property is now called, are bound to ids here, before the
        # recompute below evaluates them.
        renamed = payload.name is not None and updated.name != old_name
        bound = (
            (renamed or (effective_type == "formula" and config_changed))
            and schema_service.bind_formula_references(db, database_id)
        )
        if computed_config_changed or (renamed and bound):
            schema_service.recompute_entries(db, database_id)

        migrated |= schema_service.propagate_to_mirror(
            db, updated, old_name, old_config,
            name_changed=payload.name is not None and payload.name != old_name,
            config_changed=config_changed,
        )
        if config_changed:
            migrated |= schema_service.migrate_values_for_enabled_timeline(
                db, database_id, updated, old_config,
            )

        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="A property with that name already exists in this database",
        )
    except Exception:
        db.rollback()
        raise

    emit_entries_updated_for(db, migrated)
    return _schema_response(updated, repo.list_schemas(db, database_id))


@database_router.get(
    "/{database_id}/schemas/{schema_id}/options/{option_id}/usage",
    response_model=OptionUsageResponse,
)
def get_option_usage(
    database_id: uuid.UUID,
    schema_id: uuid.UUID,
    option_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Count the active entries that use one option of a property.

    Pre-flight for deleting an option, so the confirmation can say how many
    entries lose it. Works for select options and relation nuance options.
    """
    get_database_or_raise(db, database_id, current_user, Access.READ)
    schema = repo.get_schema(db, schema_id)
    if schema is None or schema.database_id != database_id:
        raise HTTPException(
            status_code=404,
            detail=f"Schema {schema_id} not found in database {database_id}",
        )
    return OptionUsageResponse(entries=opts.count_usage(db, schema, option_id))


@database_router.delete(
    "/{database_id}/schemas/{schema_id}",
    status_code=204,
)
def delete_schema(
    database_id: uuid.UUID,
    schema_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete a property schema and cascade-delete all associated values.

    Relations keyed on this property are reset to vanilla in the same
    transaction.  This happens unconditionally, independent of whether the
    client ran the key-references pre-flight first: the pre-flight exists so a
    confirmation dialog can name the affected relations, while this is the
    guarantee that no dangling key pointer survives the delete.

    Deleting a relation property leaves the mirror in the target database in
    place, so it needs no access to the target.

    Formulas referring to the property keep the reference as its name: they
    stay readable and bind again once a property with that name exists.

    Returns 204 No Content on success.
    """
    get_database_or_raise(db, database_id, current_user, Access.WRITE)
    schema = repo.get_schema(db, schema_id)
    if schema is None or schema.database_id != database_id:
        raise HTTPException(
            status_code=404,
            detail=f"Schema {schema_id} not found in database {database_id}",
        )
    try:
        clear_keying_for_property(db, schema_id)
        schema_service.unbind_formula_references(db, schema)
        repo.delete_schema(db, schema)
        db.commit()
    except Exception:
        db.rollback()
        raise


@database_router.get(
    "/{database_id}/schemas/{schema_id}/key-references",
    response_model=list[KeyReferenceResponse],
)
def list_key_references(
    database_id: uuid.UUID,
    schema_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List the relation properties that are keyed on this property.

    Pre-flight for destructive edits: deleting this property, or retyping it
    out of the keyable set, resets every relation listed here to vanilla.  The
    frontend calls this before it deletes so the confirmation can name the
    relations and their databases instead of warning in the abstract.

    Returns an empty list for a property nothing keys on, which is the common
    case and leaves the existing two-step delete affordance untouched.

    For a referring database the caller may not read, the relation's name and
    the database's title are returned as ``None``; the reference itself is
    still listed, since the delete resets it regardless.
    """
    get_database_or_raise(db, database_id, current_user, Access.READ)
    schema = repo.get_schema(db, schema_id)
    if schema is None or schema.database_id != database_id:
        raise HTTPException(
            status_code=404,
            detail=f"Schema {schema_id} not found in database {database_id}",
        )

    references = repo.list_relation_schemas_by_key_property(db, schema_id)
    if not references:
        return []

    # Resolve each referring relation's own database title for the dialog.
    readable = perm_repo.filter_accessible(
        db, (referrer.database_id for referrer in references), current_user, level=READ,
    )
    titles: dict[uuid.UUID, Optional[str]] = {}
    for referrer in references:
        if referrer.database_id in titles:
            continue
        if referrer.database_id not in readable:
            titles[referrer.database_id] = None
            continue
        block = repo.get_block(db, referrer.database_id)
        titles[referrer.database_id] = (
            (block.content or {}).get("title") if block is not None else None
        )

    return [
        KeyReferenceResponse(
            schema_id=referrer.id,
            schema_name=referrer.name if referrer.database_id in readable else None,
            database_id=referrer.database_id,
            database_title=titles.get(referrer.database_id),
        )
        for referrer in references
    ]


@database_router.post(
    "/{database_id}/seed-readonly-schemas",
    response_model=list[SchemaResponse],
    status_code=200,
)
def seed_readonly_schemas(
    database_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Idempotently create the system-managed property schemas for a database
    if they do not already exist.

    Schemas that already exist are left unchanged. Only newly created schemas
    are returned. The logic lives in ``repository.ensure_readonly_schemas``,
    which the view endpoints also run when a database is first displayed.

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
    get_database_or_raise(db, database_id, current_user, Access.WRITE)
    created = repo.ensure_readonly_schemas(db, database_id)

    if created:
        db.commit()
        for s in created:
            db.refresh(s)

    schemas = repo.list_schemas(db, database_id) if created else []
    return [_schema_response(s, schemas) for s in created]


# ─── Formula endpoints ────────────────────────────────────────────────────────


@database_router.get(
    "/formula-syntax",
    response_model=FormulaSyntaxResponse,
)
def get_formula_syntax(
    current_user: User = Depends(get_current_user),
):
    """
    Return the functions and the rest of the syntax a formula can use, in
    the order of the editor's syntax reference.

    The list is read from the formula engine's function registry, so it
    always names exactly the functions evaluation knows. Descriptions and
    examples are not part of it: the frontend shows them in the user's
    language from its locales, keyed by ``key``. The same for every database,
    so no database access is involved.
    """
    return FormulaSyntaxResponse(
        categories=list(SYNTAX_CATEGORIES),
        entries=[
            FormulaSyntaxEntry(
                key=entry.key,
                name=entry.name,
                category=entry.category,
                signature=entry.signature,
                insert=entry.insert,
            )
            for entry in syntax_catalogue()
        ],
    )


@database_router.post(
    "/{database_id}/formulas/validate",
    response_model=FormulaValidateResponse,
)
def validate_formula(
    database_id: uuid.UUID,
    payload: FormulaValidateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Validate a formula expression for syntax errors and return the list of
    property names it references.

    Used by the PropertySettingsModal to provide live feedback in the formula
    editor.  Does not persist anything.

    Returns
    -------
    FormulaValidateResponse
        ``valid``      – True if the expression parses without errors.
        ``error``      – Human-readable message when ``valid`` is False.
        ``prop_names`` – Property names referenced via ``prop('Name')``.
    """
    get_database_or_raise(db, database_id, current_user, Access.READ)
    from app.blocks.formula_engine import extract_prop_names

    try:
        validate_syntax(payload.expression)
        names = extract_prop_names(payload.expression)
        return FormulaValidateResponse(valid=True, prop_names=names)
    except FormulaError as exc:
        return FormulaValidateResponse(valid=False, error=str(exc))
