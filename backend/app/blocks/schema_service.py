"""
Property schema operations behind the schema endpoints.

The endpoints in ``app.blocks.database_router`` check access, run these steps
inside one transaction, commit and broadcast. Nothing here commits.

Computed properties
-------------------
Formula and rollup configs are validated (syntax, known rollup function) and
checked for circular dependencies with Kahn's topological sort before they
are stored (:func:`check_computed_config`). When a computed config changes,
or a rename rewrites the ``prop()`` references of formulas, every entry of the
database is re-evaluated (:func:`recompute_entries`).

Bilateral relations
-------------------
Creating a bilateral relation creates its mirror property in the target
database at once (:func:`create_eager_mirror`). Updating one propagates to
the mirror (:func:`propagate_to_mirror`): a changed mirror name renames the
mirror, a changed own name updates the mirror's back-pointer, and the
timeline flag and the nuance configuration, which are edge data both sides
read, are copied across. Keying is not propagated; it is a read-side pointer
each side configures on its own.

Timelines
---------
Enabling ``hasTimeline`` on a property with values wraps every existing value
into an always-valid slot (:func:`migrate_values_for_enabled_timeline`), on
both sides of a bilateral relation.
"""
import uuid
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.blocks import options as opts
from app.blocks import relation_lock
from app.blocks import repository as repo
from app.blocks import views
from app.blocks.computed import (
    _ROLLUP_FUNCTIONS,
    SchemaLike,
    build_dependency_graph,
    compute_all_for_entry,
    has_any_cycle,
)
from app.blocks.formula_engine import FormulaError, rename_prop_in_expression, validate_syntax
from app.blocks.models import PropertySchema
from app.blocks.readonly_properties import READONLY_TYPES
from app.blocks.relation_sync import KEYABLE_PROPERTY_TYPES, ensure_bilateral_mirror
from app.blocks.timeline import (
    migrate_relation_values_to_timeline,
    migrate_scalar_values_to_timeline,
)

COMPUTED_TYPES = frozenset({"formula", "rollup"})


# ─── Validation ───────────────────────────────────────────────────────────────


def would_create_cycle(
    db: Session,
    database_id: uuid.UUID,
    proposed_id: uuid.UUID,
    proposed_name: str,
    proposed_type: str,
    proposed_config: Optional[dict],
) -> bool:
    """
    Return True if saving a formula / rollup schema with the proposed
    attributes would introduce a circular dependency.

    The check builds the full dependency graph including the proposed schema
    (replacing the old version if it already exists) and runs Kahn's
    topological sort over it.
    """
    existing = repo.list_schemas(db, database_id)
    # Remove the schema being updated so we replace it with the proposal
    schemas_without: list = [s for s in existing if s.id != proposed_id]

    sentinel = SchemaLike(
        id=proposed_id,
        name=proposed_name,
        type=proposed_type,
        config=proposed_config,
    )
    full_schemas = schemas_without + [sentinel]
    graph = build_dependency_graph(full_schemas)
    return has_any_cycle(graph)


def validate_computed_config(
    payload_type: str,
    payload_config: Optional[dict],
) -> None:
    """
    Validate formula expression syntax or rollup completeness.

    Raises ``HTTPException(422)`` on any detected issue.
    """
    if payload_type == "formula" and payload_config:
        expression = payload_config.get("expression", "")
        if expression:
            try:
                validate_syntax(expression)
            except FormulaError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"Formula syntax error: {exc}",
                ) from exc

    if payload_type == "rollup" and payload_config:
        function = payload_config.get("function", "")
        valid_fns = _ROLLUP_FUNCTIONS
        if function and function not in valid_fns:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown rollup function '{function}'. "
                       f"Valid options: {', '.join(sorted(valid_fns))}",
            )

def check_computed_config(
    db: Session,
    database_id: uuid.UUID,
    schema_id: uuid.UUID,
    name: str,
    type_: str,
    config: Optional[dict],
) -> None:
    """
    Refuse a formula or rollup config with 422 when it is malformed or would
    make the property depend on itself, directly or through other ones.
    """
    validate_computed_config(type_, config)
    if would_create_cycle(db, database_id, schema_id, name, type_, config):
        raise HTTPException(
            status_code=422,
            detail="This formula / rollup creates a circular dependency",
        )


def option_changes(
    old_type: str,
    new_type: str,
    old_config: dict,
    new_config: Optional[dict],
) -> tuple[set[str], bool]:
    """
    The option ids a config update removes, and whether it relabels select
    options.

    Only a config edit on an unchanged type removes options; a retype leaves
    the stored values alone.
    """
    if new_config is None or new_type != old_type:
        return set(), False
    removed = opts.removed_ids(new_type, old_config, new_config)
    relabelled = new_type == opts.SELECT_TYPE and opts.labels_changed(
        new_type, old_config, new_config,
    )
    return removed, relabelled


def retyped_out_of_keyable(old_type: str, new_type: Optional[str]) -> bool:
    """
    Whether a retype takes the property out of the types a relation may be
    keyed on, so every relation keyed on it has to be reset.
    """
    return (
        new_type is not None
        and new_type != old_type
        and old_type in KEYABLE_PROPERTY_TYPES
        and new_type not in KEYABLE_PROPERTY_TYPES
    )


def lock_for_relation_config(
    db: Session,
    database_id: uuid.UUID,
    schema: PropertySchema,
    proposed: SchemaLike,
) -> None:
    """
    Take the relation locks a config change of a relation needs.

    Such a change may rewrite its values and its mirror's (timeline
    migration, removed nuance options), so it waits for value writes to
    either side, as they wait for it. Both the current and the proposed
    target are locked.
    """
    relation_lock.lock_relation_databases(
        db,
        relation_lock.databases_for_schema(database_id, schema)
        | relation_lock.databases_for_schema(database_id, proposed),
    )


# ─── Options ──────────────────────────────────────────────────────────────────


def purge_options(db: Session, schema: PropertySchema, removed: set[str]) -> bool:
    """
    Remove deleted options of *schema* from its values, from the filters of
    its database's views and from automations. Returns True when a stored
    value changed, so the caller broadcasts an entries update.
    """
    changed = opts.purge_values(db, schema, removed)
    views.forget_options_in_views(db, schema.database_id, schema.id, removed)
    opts.purge_automations(db, schema.id, removed)
    return changed > 0


# ─── Creation ─────────────────────────────────────────────────────────────────


def schema_position_after_last(db: Session, database_id: uuid.UUID) -> float:
    """Return a position that places a new schema after all existing ones."""
    schemas = repo.list_schemas(db, database_id)
    if not schemas:
        return 1.0
    return schemas[-1].position + 1.0


def create_eager_mirror(
    db: Session,
    schema: PropertySchema,
    config: Optional[dict],
) -> Optional[uuid.UUID]:
    """
    Create the mirror property of a new bilateral relation in its target
    database, so it exists before any entry is linked.

    *config* is the config the client sent. Returns the target database id
    when a mirror was created, so the caller can announce the new property
    there; ``None`` when the property is no bilateral relation, the config
    names no target or mirror, or the mirror already existed.
    """
    cfg = config or {}
    if schema.type != "relation" or cfg.get("direction") != "bilateral":
        return None
    target_db_id_raw = cfg.get("target_database_id")
    mirror_name_raw = cfg.get("mirror_property_name")
    if not (target_db_id_raw and mirror_name_raw):
        return None
    try:
        target_db_id = uuid.UUID(str(target_db_id_raw))
    except ValueError:
        return None
    if ensure_bilateral_mirror(db, schema, target_db_id, str(mirror_name_raw)):
        return target_db_id
    return None


# ─── Formulas ─────────────────────────────────────────────────────────────────


def rename_formula_references(
    db: Session,
    database_id: uuid.UUID,
    old_name: str,
    new_name: str,
) -> bool:
    """
    Point the ``prop()`` references of every formula in *database_id* at a
    renamed property. Returns True when an expression changed.

    Formulas reference properties by name (``prop("Name")``), so renaming a
    property would orphan every ``prop("OldName")``: it silently resolves to
    empty and the dependent formula column goes blank. Rollups are unaffected,
    they reference their columns by id.
    """
    rewrote = False
    for sch in repo.list_schemas(db, database_id):
        if sch.type != "formula":
            continue
        cfg = dict(sch.config or {})
        expr = cfg.get("expression")
        if not isinstance(expr, str) or not expr:
            continue
        new_expr = rename_prop_in_expression(expr, old_name, new_name)
        if new_expr != expr:
            cfg["expression"] = new_expr
            repo.update_schema(db, sch, config=cfg)
            rewrote = True
    return rewrote


def recompute_entries(db: Session, database_id: uuid.UUID) -> None:
    """Re-evaluate every computed property of every entry of *database_id*."""
    entries = repo.list_children(
        db, database_id, state="active",
        exclude_types=frozenset({"entry_template"}),
    )
    for entry in entries:
        compute_all_for_entry(db, database_id, entry.id)


# ─── Bilateral relations ──────────────────────────────────────────────────────


def _mirror_target(config: dict, old_config: dict) -> Optional[uuid.UUID]:
    """The target database of a relation, from its new or its old config."""
    raw = config.get("target_database_id") or old_config.get("target_database_id")
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None


def _current_mirror(
    db: Session,
    target_db_id: uuid.UUID,
    new_mirror_name: Optional[str],
    old_mirror_name: Optional[str],
):
    """The mirror property under its current name, or None."""
    current_mirror_name = new_mirror_name or old_mirror_name
    if not current_mirror_name:
        return None
    return repo.get_schema_by_name(db, target_db_id, current_mirror_name)


def _mirrored_nuance(source_nuance: dict) -> dict:
    """
    The nuance config of the mirror side for the source's *source_nuance*.

    Each side stores its own affixes and orientation at the top level; the
    option set is shared. The ``synced`` sub-object holds the other side's
    framing, so the source's ``synced`` becomes the mirror's own framing and
    the source's own framing becomes the mirror's ``synced``. Reopening the
    settings on either side then shows both sides correctly.
    """
    if not source_nuance.get("enabled"):
        return {"enabled": False}
    synced = source_nuance.get("synced") or {}
    return {
        "enabled": True,
        "options": source_nuance.get("options") or [],
        "affix1": synced.get("affix1", ""),
        "affix2": synced.get("affix2", ""),
        "orientation": synced.get("orientation", "prepended"),
        "synced": {
            "affix1": source_nuance.get("affix1", ""),
            "affix2": source_nuance.get("affix2", ""),
            "orientation": source_nuance.get("orientation", "prepended"),
        },
    }


def propagate_to_mirror(
    db: Session,
    updated: PropertySchema,
    old_name: str,
    old_config: dict,
    *,
    name_changed: bool,
    config_changed: bool,
) -> set[uuid.UUID]:
    """
    Carry an update of a bilateral relation over to its mirror property.

    A) A changed ``mirror_property_name`` renames the mirror, so its values
       are kept.
    B) A changed own name updates the mirror's back-pointer
       (``config.mirror_property_name``), so it still resolves to this one.
    C) A toggled ``hasTimeline`` is copied, so both sides use the same data
       model.
    D) A changed nuance config is copied with the framing swapped (see
       :func:`_mirrored_nuance`); nuance options removed here are removed from
       the mirror's values too, since both sides share the option set.

    There is deliberately no case for keying. Timeline slots and nuance
    labels are edge data both sides read, so the sides must agree on them.
    Keying is a read-side pointer into the target database and touches no
    edge: a relation keyed on a date of the target, alongside its mirror
    keyed on a different date of this database, is a valid pairing.
    Propagating it would collapse that into one shared setting and write a
    ``key_property_id`` of one database into a relation pointing at the
    other, where it resolves to nothing.

    Finally the mirror is created if it does not exist, which covers a
    relation turned from unilateral into bilateral.

    *name_changed* and *config_changed* tell which parts of the update were
    sent. Returns the databases whose entry values changed (removed nuance
    options on the mirror side).
    """
    migrated: set[uuid.UUID] = set()
    new_config = updated.config or {}
    if updated.type != "relation" or new_config.get("direction") != "bilateral":
        return migrated
    target_db_id = _mirror_target(new_config, old_config)
    if target_db_id is None:
        return migrated

    old_mirror_name: Optional[str] = old_config.get("mirror_property_name")
    new_mirror_name: Optional[str] = new_config.get("mirror_property_name")

    # A) The mirror's name changed.
    if (
        config_changed
        and old_mirror_name
        and new_mirror_name
        and old_mirror_name != new_mirror_name
    ):
        old_mirror = repo.get_schema_by_name(db, target_db_id, old_mirror_name)
        if old_mirror is not None:
            repo.update_schema(db, old_mirror, name=new_mirror_name)

    # B) This property's name changed. The mirror is found under its current
    # name, which A may just have changed.
    if name_changed:
        mirror = _current_mirror(db, target_db_id, new_mirror_name, old_mirror_name)
        if mirror is not None:
            mirror_cfg = dict(mirror.config or {})
            mirror_cfg["mirror_property_name"] = updated.name
            repo.update_schema(db, mirror, config=mirror_cfg)

    # C) The timeline flag changed.
    new_has_timeline = new_config.get("hasTimeline", False)
    if config_changed and old_config.get("hasTimeline", False) != new_has_timeline:
        mirror = _current_mirror(db, target_db_id, new_mirror_name, old_mirror_name)
        if mirror is not None:
            mirror_cfg = dict(mirror.config or {})
            mirror_cfg["hasTimeline"] = new_has_timeline
            repo.update_schema(db, mirror, config=mirror_cfg)

    # D) The nuance config changed.
    if config_changed and old_config.get("nuance") != new_config.get("nuance"):
        mirror = _current_mirror(db, target_db_id, new_mirror_name, old_mirror_name)
        if mirror is not None:
            mirror_old_cfg = dict(mirror.config or {})
            mirror_cfg = dict(mirror.config or {})
            mirror_cfg["nuance"] = _mirrored_nuance(new_config.get("nuance") or {})
            repo.update_schema(db, mirror, config=mirror_cfg)
            mirror_removed = opts.removed_ids(opts.RELATION_TYPE, mirror_old_cfg, mirror_cfg)
            if mirror_removed and purge_options(db, mirror, mirror_removed):
                migrated.add(mirror.database_id)

    current_mirror_name = new_mirror_name or old_mirror_name
    if current_mirror_name:
        ensure_bilateral_mirror(db, updated, target_db_id, current_mirror_name)
    return migrated


# ─── Timelines ────────────────────────────────────────────────────────────────


def migrate_values_for_enabled_timeline(
    db: Session,
    database_id: uuid.UUID,
    updated: PropertySchema,
    old_config: dict,
) -> set[uuid.UUID]:
    """
    Wrap existing values into the timeline format when an update enables
    ``hasTimeline``. Returns the databases whose entry values changed.

    Without this, flat values become invisible in the "all" display mode,
    which renders only ``_timeline`` slots, even though the "last" mode still
    resolves them. Every existing value becomes an always-valid (``""``) slot,
    as the frontend warns before the user confirms the toggle.

    Relation properties store links in the pool format and are converted by
    the relation helper, on both sides of a bilateral relation
    (:func:`propagate_to_mirror` has already set the flag on the mirror).
    Every other property type is wrapped by the scalar helper; the system
    managed types and ``parent_item`` / ``sub_item`` carry no timeline.
    """
    migrated: set[uuid.UUID] = set()
    new_config = updated.config or {}
    if old_config.get("hasTimeline", False) or not new_config.get("hasTimeline", False):
        return migrated

    if updated.type == "relation":
        migrate_relation_values_to_timeline(db, database_id, updated.id)
        migrated.add(database_id)
        if new_config.get("direction") == "bilateral":
            target_db_id = _mirror_target(new_config, old_config)
            if target_db_id is not None:
                mirror_name = (
                    new_config.get("mirror_property_name")
                    or old_config.get("mirror_property_name")
                    or updated.name
                )
                mirror = repo.get_schema_by_name(db, target_db_id, mirror_name)
                if mirror is not None:
                    migrate_relation_values_to_timeline(db, target_db_id, mirror.id)
                    migrated.add(target_db_id)
    elif (
        updated.type not in relation_lock.RELATION_FAMILY_TYPES
        and updated.type not in READONLY_TYPES
    ):
        migrate_scalar_values_to_timeline(db, database_id, updated.id)
        migrated.add(database_id)
    return migrated
