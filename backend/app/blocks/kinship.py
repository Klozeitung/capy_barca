"""
The kinship set: the properties a family tree reads.

A database that describes people can carry a fixed set of properties with
reserved names. The family tree view of a later release reads them; until a
database has such a view, the set is dormant and the properties behave like
any other, apart from the guards below.

Roles and reserved names
------------------------
Every role has a reserved name per language (:data:`RESERVED_NAMES`). A
property whose name matches one of them (ignoring case and surrounding
spaces) and whose structure fits the role (:func:`fits`) carries the role in
``config.kinRole``. The marker is set and removed by the repository whenever a
property is created or written (:func:`apply_markers`); a client never sets
it (:func:`keep_server_owned_keys`). A database holds each role at most once.

Structure per role:

* ``parents`` / ``children``: a bilateral relation to the database itself
  whose mirror carries the reserved name of the other role.
* ``partners``, ``siblings``, ``half_siblings``: a ``bilateral_self``
  relation to the database itself.
* ``birth`` / ``death``: a date, rollup or formula property.
* ``sex``: a select property. The option that means "female" is pointed at by
  ``config.kinFemaleOptionId``, also kept by the server alone.

A marked property cannot be renamed, retyped or keyed, and its structure
cannot change (:func:`check_update`). While a family tree view exists in its
database, it cannot be deleted either, and ``parents``, ``children`` and
``partners`` keep their timeline and nuance switched on.

System nuances
--------------
The nuance options of the relation roles are not stored on the properties.
They are a fixed catalogue with stable ids (:data:`BASE_NUANCES`), extended
by installation-wide aliases an administrator maintains (``app_settings``
under :data:`ALIASES_KEY`). The API returns them in the caller's language as
``config.nuance.options`` (:func:`with_system_nuances`); options a client
sends for such a property are dropped (:func:`without_client_nuance_options`).
An alias counts as its base nuance; siblings have no base nuance, so their
aliases are labels only.

Completion
----------
Creating or changing a family tree view completes the set of its database
(:func:`ensure_set`): missing properties are created in the caller's
language, and the relations a tree draws get a timeline and nuances.
"""
import uuid
from typing import Any, Iterable, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.blocks import options as opts
from app.blocks.models import DatabaseView, PropertySchema
from app.settings.model import AppSetting

ROLE_KEY = "kinRole"
FEMALE_OPTION_KEY = "kinFemaleOptionId"
SERVER_OWNED_KEYS = (ROLE_KEY, FEMALE_OPTION_KEY)

FAMILY_TREE_VIEW = "family_tree"

PARENTS = "parents"
CHILDREN = "children"
PARTNERS = "partners"
SIBLINGS = "siblings"
HALF_SIBLINGS = "half_siblings"
BIRTH = "birth"
DEATH = "death"
SEX = "sex"

#: Every role, in the order a missing set is created.
ROLES = (PARENTS, CHILDREN, PARTNERS, SIBLINGS, HALF_SIBLINGS, BIRTH, DEATH, SEX)

LANGUAGES = ("de", "en")
DEFAULT_LANGUAGE = "en"

RESERVED_NAMES: dict[str, dict[str, str]] = {
    PARENTS: {"de": "Eltern", "en": "Parents"},
    CHILDREN: {"de": "Kinder", "en": "Children"},
    PARTNERS: {"de": "Partner", "en": "Partners"},
    SIBLINGS: {"de": "Geschwister", "en": "Siblings"},
    HALF_SIBLINGS: {"de": "Halbgeschwister", "en": "Half-siblings"},
    BIRTH: {"de": "Geburtsdatum", "en": "Date of birth"},
    DEATH: {"de": "Todeszeitpunkt", "en": "Time of death"},
    SEX: {"de": "Geschlecht (Biologisch)", "en": "Sex (biological)"},
}

#: The options a new ``sex`` property starts with, female first.
SEX_OPTIONS: tuple[dict[str, str], ...] = (
    {"de": "weiblich", "en": "female"},
    {"de": "männlich", "en": "male"},
)

RELATION_ROLES = (PARENTS, CHILDREN, PARTNERS, SIBLINGS, HALF_SIBLINGS)
MIRRORED_ROLES = {PARENTS: CHILDREN, CHILDREN: PARENTS}
SELF_ROLES = (PARTNERS, SIBLINGS, HALF_SIBLINGS)
DATE_ROLES = (BIRTH, DEATH)
DATE_TYPES = ("date", "rollup", "formula")

#: Roles a family tree draws edges from; they keep timeline and nuance while
#: a family tree view exists.
TREE_EDGE_ROLES = (PARENTS, CHILDREN, PARTNERS)

# ─── Nuance groups ────────────────────────────────────────────────────────────

GROUP_PARENTS = "parents"
GROUP_PARTNERS = "partners"
GROUP_SIBLINGS = "siblings"
GROUP_HALF_SIBLINGS = "half_siblings"
GROUPS = (GROUP_PARENTS, GROUP_PARTNERS, GROUP_SIBLINGS, GROUP_HALF_SIBLINGS)

_ROLE_GROUPS = {
    PARENTS: GROUP_PARENTS,
    CHILDREN: GROUP_PARENTS,
    PARTNERS: GROUP_PARTNERS,
    SIBLINGS: GROUP_SIBLINGS,
    HALF_SIBLINGS: GROUP_HALF_SIBLINGS,
}

#: The fixed nuances per group, in display order. Ids never change; values
#: store them.
BASE_NUANCES: dict[str, tuple[dict[str, str], ...]] = {
    GROUP_PARENTS: (
        {"id": "kin.parent.biological", "de": "leiblich", "en": "biological"},
        {"id": "kin.parent.putative", "de": "vorgeblich", "en": "putative"},
        {"id": "kin.parent.legal", "de": "rechtlich", "en": "legal"},
        {"id": "kin.parent.adoptive", "de": "adoptiv", "en": "adoptive"},
        {"id": "kin.parent.foster", "de": "Pflege", "en": "foster"},
        {"id": "kin.parent.step", "de": "Stief", "en": "step"},
    ),
    GROUP_PARTNERS: (
        {"id": "kin.partner.marriage", "de": "Ehe", "en": "marriage"},
        {"id": "kin.partner.engagement", "de": "Verlobung", "en": "engagement"},
        {"id": "kin.partner.relationship", "de": "Beziehung", "en": "relationship"},
        {"id": "kin.partner.affair", "de": "Affäre", "en": "affair"},
        {"id": "kin.partner.concubinage", "de": "Konkubinat", "en": "concubinage"},
    ),
    GROUP_SIBLINGS: (),
    GROUP_HALF_SIBLINGS: (),
}

ALIASES_KEY = "kinship_nuances"
MAX_ALIAS_LABEL = 100
MAX_ALIASES = 200


# ─── Names and languages ──────────────────────────────────────────────────────


def _fold(name: Any) -> str:
    return name.strip().casefold() if isinstance(name, str) else ""


_NAME_ROLES = {
    _fold(name): role
    for role, names in RESERVED_NAMES.items()
    for name in names.values()
}


def role_for_name(name: Any) -> Optional[str]:
    """The role whose reserved name *name* is, in any language, or None."""
    return _NAME_ROLES.get(_fold(name))


def language_for(user_language: Optional[str], accept_language: Optional[str] = None) -> str:
    """
    The language set properties and nuance labels are given in: the
    account's, else the first supported one of an ``Accept-Language`` header,
    else English.
    """
    if user_language in LANGUAGES:
        return user_language
    for part in (accept_language or "").split(","):
        code = part.split(";", 1)[0].strip().lower()[:2]
        if code in LANGUAGES:
            return code
    return DEFAULT_LANGUAGE


def reserved_name(role: str, language: str) -> str:
    return RESERVED_NAMES[role].get(language) or RESERVED_NAMES[role][DEFAULT_LANGUAGE]


def role_of(schema: Any) -> Optional[str]:
    """The role *schema* carries (``config.kinRole``), or None."""
    role = (getattr(schema, "config", None) or {}).get(ROLE_KEY)
    return role if role in ROLES else None


def nuance_group(role: Optional[str]) -> Optional[str]:
    """The system nuance group of a relation role, None for other roles."""
    return _ROLE_GROUPS.get(role) if role else None


# ─── Structure ────────────────────────────────────────────────────────────────


def _option_ids(config: dict) -> set[str]:
    return {
        o["id"] for o in config.get("options") or []
        if isinstance(o, dict) and isinstance(o.get("id"), str)
    }


def fits(role: str, type_: str, config: Optional[dict], database_id: uuid.UUID) -> bool:
    """Whether a property of *type_* with *config* has the structure of *role*."""
    cfg = config or {}
    if role in DATE_ROLES:
        return type_ in DATE_TYPES
    if role == SEX:
        return type_ == "select"
    if type_ != "relation" or str(cfg.get("target_database_id") or "") != str(database_id):
        return False
    if role in SELF_ROLES:
        return cfg.get("direction") == "bilateral_self"
    return (
        cfg.get("direction") == "bilateral"
        and role_for_name(cfg.get("mirror_property_name")) == MIRRORED_ROLES[role]
    )


def describe_structure(role: str) -> str:
    """The structure *role* needs, for an error message."""
    if role in DATE_ROLES:
        return "a date, rollup or formula property"
    if role == SEX:
        return "a select property"
    if role in SELF_ROLES:
        return "a relation to this database that relates entries to each other"
    other = RESERVED_NAMES[MIRRORED_ROLES[role]]
    return (
        "a two-way relation to this database whose mirror property is named "
        f"'{other['de']}' or '{other['en']}'"
    )


def _female_option(config: dict, previous: Optional[str]) -> Optional[str]:
    """The option a ``sex`` property means "female" by: the one marked before
    while it exists, else the first one labelled female in any language."""
    ids = _option_ids(config)
    if previous in ids:
        return previous
    labels = {_fold(option[lang]) for option in SEX_OPTIONS[:1] for lang in LANGUAGES}
    for option in config.get("options") or []:
        if isinstance(option, dict) and _fold(option.get("label")) in labels:
            return option.get("id")
    return None


def marked_config(
    schema: Any, others: Iterable[Any],
) -> Optional[dict]:
    """
    *schema*'s config with the markers it should carry, given the *others*
    properties of its database. Returns None when nothing changes.

    The role goes to a property whose name is reserved for it and whose
    structure fits, unless another property of the database holds it.
    """
    config = dict(getattr(schema, "config", None) or {})
    role = role_for_name(schema.name)
    if role is not None and not fits(role, schema.type, config, schema.database_id):
        role = None
    if role is not None and any(
        o.id != schema.id and role_of(o) == role for o in others
    ):
        role = None

    wanted = dict(config)
    wanted.pop(ROLE_KEY, None)
    wanted.pop(FEMALE_OPTION_KEY, None)
    if role is not None:
        wanted[ROLE_KEY] = role
        if role == SEX:
            female = _female_option(config, config.get(FEMALE_OPTION_KEY))
            if female:
                wanted[FEMALE_OPTION_KEY] = female
    return None if wanted == config else wanted


def apply_markers(db: Session, schema: PropertySchema) -> None:
    """Bring the markers of *schema* in line with its name and structure."""
    config = schema.config or {}
    if role_for_name(schema.name) is None and not any(k in config for k in SERVER_OWNED_KEYS):
        return
    others = db.scalars(
        select(PropertySchema).where(
            PropertySchema.database_id == schema.database_id,
            PropertySchema.id != schema.id,
        )
    ).all()
    updated = marked_config(schema, others)
    if updated is not None:
        schema.config = updated
        db.flush()


def keep_server_owned_keys(old_config: Optional[dict], new_config: dict) -> dict:
    """*new_config* with the markers of *old_config* instead of the client's."""
    result = {k: v for k, v in new_config.items() if k not in SERVER_OWNED_KEYS}
    for key in SERVER_OWNED_KEYS:
        if key in (old_config or {}):
            result[key] = old_config[key]
    return result


def strip_markers(config: Optional[dict]) -> Optional[dict]:
    """*config* without the markers, for a property leaving its database."""
    if not isinstance(config, dict) or not any(k in config for k in SERVER_OWNED_KEYS):
        return config
    return {k: v for k, v in config.items() if k not in SERVER_OWNED_KEYS}


# ─── Guards ───────────────────────────────────────────────────────────────────


def has_family_tree(db: Session, database_id: uuid.UUID) -> bool:
    """Whether *database_id* has a family tree view."""
    return db.scalar(
        select(DatabaseView.id).where(
            DatabaseView.database_id == database_id,
            DatabaseView.view_type == FAMILY_TREE_VIEW,
        ).limit(1)
    ) is not None


def _holder(db: Session, database_id: uuid.UUID, role: str, exclude: Optional[uuid.UUID]):
    for schema in db.scalars(
        select(PropertySchema).where(PropertySchema.database_id == database_id)
    ):
        if schema.id != exclude and role_of(schema) == role:
            return schema
    return None


def check_new_name(
    db: Session,
    database_id: uuid.UUID,
    name: str,
    type_: str,
    config: Optional[dict],
    schema_id: Optional[uuid.UUID] = None,
) -> None:
    """
    Refuse a property that takes a reserved name without fitting the role
    (422), or whose role another property of the database holds (409).
    *schema_id* is the property being renamed, if any.
    """
    role = role_for_name(name)
    if role is None:
        return
    holder = _holder(db, database_id, role, schema_id)
    if holder is not None:
        raise HTTPException(
            status_code=409,
            detail=f"'{holder.name}' already holds the family role of '{name.strip()}'",
        )
    if not fits(role, type_, config, database_id):
        raise HTTPException(
            status_code=422,
            detail=f"The name '{name.strip()}' is reserved for {describe_structure(role)}",
        )


def check_update(
    db: Session,
    schema: PropertySchema,
    *,
    new_name: Optional[str],
    new_type: Optional[str],
    new_config: Optional[dict],
) -> None:
    """
    Refuse with 422 a change a marked property does not allow: a rename, a
    retype, a structure that no longer fits, keying, the loss of the option
    marked female, and, while a family tree view exists, switching off the
    timeline or the nuance of an edge role.
    """
    role = role_of(schema)
    if role is None:
        return
    if new_name is not None and new_name != schema.name:
        raise HTTPException(
            status_code=422, detail="A property of the family set cannot be renamed",
        )
    effective_type = new_type if new_type is not None else schema.type
    if effective_type != schema.type:
        raise HTTPException(
            status_code=422, detail="A property of the family set cannot change its type",
        )
    if new_config is None:
        return
    if not fits(role, effective_type, new_config, schema.database_id):
        raise HTTPException(
            status_code=422,
            detail=f"'{schema.name}' must stay {describe_structure(role)}",
        )
    keying = new_config.get("keying")
    if isinstance(keying, dict) and keying.get("enabled") is True:
        raise HTTPException(
            status_code=422, detail="A property of the family set cannot be keyed",
        )
    if role == SEX:
        female = (schema.config or {}).get(FEMALE_OPTION_KEY)
        if female and female not in _option_ids(new_config):
            raise HTTPException(
                status_code=422,
                detail="The option the family tree reads as female cannot be removed",
            )
    if role in TREE_EDGE_ROLES and has_family_tree(db, schema.database_id):
        nuance = new_config.get("nuance")
        nuance_on = isinstance(nuance, dict) and nuance.get("enabled") is True
        if not new_config.get("hasTimeline") or not nuance_on:
            raise HTTPException(
                status_code=422,
                detail="A family tree uses this relation; its timeline and nuances stay on",
            )


def check_delete(db: Session, schema: PropertySchema) -> None:
    """Refuse with 422 deleting a marked property a family tree reads."""
    if role_of(schema) is not None and has_family_tree(db, schema.database_id):
        raise HTTPException(
            status_code=422,
            detail="A family tree uses this property; it cannot be deleted",
        )


# ─── System nuances ───────────────────────────────────────────────────────────


def load_aliases(db: Session) -> list[dict]:
    """The stored aliases, ``[{id, group, base, label}]``."""
    row = db.get(AppSetting, ALIASES_KEY)
    raw = (row.value or {}).get("aliases") if row is not None and isinstance(row.value, dict) else None
    result = []
    for item in raw if isinstance(raw, list) else []:
        if (
            isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and item.get("group") in GROUPS
            and isinstance(item.get("label"), str)
        ):
            result.append({
                "id": item["id"],
                "group": item["group"],
                "base": item.get("base") if isinstance(item.get("base"), str) else None,
                "label": item["label"],
            })
    return result


def store_aliases(db: Session, aliases: list[dict]) -> None:
    row = db.get(AppSetting, ALIASES_KEY)
    value = {"aliases": aliases}
    if row is None:
        db.add(AppSetting(key=ALIASES_KEY, value=value))
    else:
        row.value = value
    db.flush()


def base_ids(group: str) -> list[str]:
    return [n["id"] for n in BASE_NUANCES.get(group, ())]


def base_options(group: str, language: str) -> list[dict]:
    """The base nuances of *group* as options in *language*."""
    lang = language if language in LANGUAGES else DEFAULT_LANGUAGE
    return [{"id": n["id"], "label": n[lang]} for n in BASE_NUANCES.get(group, ())]


def catalogue(aliases: list[dict], language: str) -> dict[str, list[dict]]:
    """Every group's nuance options in *language*: base nuances, then aliases."""
    result = {group: base_options(group, language) for group in GROUPS}
    for alias in aliases:
        result[alias["group"]].append({"id": alias["id"], "label": alias["label"]})
    return result


def with_system_nuances(config: Optional[dict], options: Optional[dict[str, list[dict]]]) -> Optional[dict]:
    """
    *config* as the API returns it: a marked relation offers its group's
    system nuances as ``nuance.options``. *options* is a :func:`catalogue`;
    without one the config is returned unchanged.
    """
    if options is None or not isinstance(config, dict):
        return config
    group = nuance_group(config.get(ROLE_KEY))
    if group is None:
        return config
    nuance = config.get("nuance")
    nuance = dict(nuance) if isinstance(nuance, dict) else {"enabled": False}
    nuance["options"] = [dict(o) for o in options.get(group, [])]
    return {**config, "nuance": nuance}


def without_client_nuance_options(name: str, type_: str, config: Optional[dict]) -> Optional[dict]:
    """
    *config* without nuance options when the property takes a relation role:
    those come from the catalogue, never from the client.
    """
    if type_ != "relation" or not isinstance(config, dict):
        return config
    if nuance_group(role_for_name(name)) is None:
        return config
    nuance = config.get("nuance")
    if not isinstance(nuance, dict) or "options" not in nuance:
        return config
    return {**config, "nuance": {**nuance, "options": []}}


def marked_relations(db: Session, group: str) -> list[PropertySchema]:
    """Every marked relation of *group*, in all databases."""
    return [
        schema for schema in db.scalars(
            select(PropertySchema).where(PropertySchema.type == "relation")
        )
        if nuance_group(role_of(schema)) == group
    ]


def normalize_aliases(raw: list[dict], previous: list[dict]) -> list[dict]:
    """
    The aliases a client sent, checked and in canonical form.

    An alias of a group with base nuances names one of them as ``base``; an
    alias of the sibling groups has none. Labels are trimmed and must be
    unique within a group, base labels in every language included. An alias
    without id gets a new one; an existing id keeps its group. Raises 422.
    """
    if len(raw) > MAX_ALIASES:
        raise HTTPException(status_code=422, detail="Too many nuances")
    groups_by_id = {a["id"]: a["group"] for a in previous}
    taken: dict[str, set[str]] = {
        group: {_fold(n[lang]) for n in BASE_NUANCES[group] for lang in LANGUAGES}
        for group in GROUPS
    }
    seen_ids: set[str] = set()
    result = []
    for item in raw:
        group = item.get("group")
        if group not in GROUPS:
            raise HTTPException(status_code=422, detail=f"Unknown nuance group {group!r}")
        label = (item.get("label") or "").strip()
        if not label or len(label) > MAX_ALIAS_LABEL:
            raise HTTPException(status_code=422, detail="A nuance needs a label of up to 100 characters")
        if _fold(label) in taken[group]:
            raise HTTPException(status_code=422, detail=f"The nuance '{label}' exists already")
        base = item.get("base") or None
        bases = base_ids(group)
        if bases and base not in bases:
            raise HTTPException(status_code=422, detail=f"The nuance '{label}' needs a base nuance")
        if not bases and base is not None:
            raise HTTPException(status_code=422, detail=f"The group {group!r} has no base nuances")
        alias_id = item.get("id") or None
        if alias_id is not None and groups_by_id.get(alias_id) != group:
            if alias_id in groups_by_id:
                raise HTTPException(status_code=422, detail="A nuance cannot change its group")
            alias_id = None
        if alias_id is None or alias_id in seen_ids:
            alias_id = str(uuid.uuid4())
        seen_ids.add(alias_id)
        taken[group].add(_fold(label))
        result.append({"id": alias_id, "group": group, "base": base, "label": label})
    return result


def replacements_for_removed(previous: list[dict], current: list[dict]) -> dict[str, dict[str, Optional[str]]]:
    """
    Per group, the aliases *current* no longer holds and what their values
    become: the base nuance, or None (removed) for groups without one.
    """
    kept = {a["id"] for a in current}
    result: dict[str, dict[str, Optional[str]]] = {}
    for alias in previous:
        if alias["id"] not in kept:
            result.setdefault(alias["group"], {})[alias["id"]] = alias["base"]
    return result


# ─── Completion ───────────────────────────────────────────────────────────────


def _new_config(role: str, database_id: uuid.UUID, language: str) -> tuple[str, dict]:
    target = str(database_id)
    if role in MIRRORED_ROLES:
        return "relation", {
            "target_database_id": target,
            "direction": "bilateral",
            "mirror_property_name": reserved_name(MIRRORED_ROLES[role], language),
        }
    if role in SELF_ROLES:
        return "relation", {"target_database_id": target, "direction": "bilateral_self"}
    if role in DATE_ROLES:
        return "date", {}
    return "select", {
        "options": [
            {"id": str(uuid.uuid4()), "label": option[language]} for option in SEX_OPTIONS
        ],
    }


def _edges_on(config: dict) -> dict:
    """*config* with timeline and nuance switched on, and keying off."""
    nuance = config.get("nuance")
    nuance = dict(nuance) if isinstance(nuance, dict) else {}
    nuance["enabled"] = True
    nuance["options"] = []
    result = {**config, "hasTimeline": True, "nuance": nuance}
    result.pop("keying", None)
    return result


def ensure_set(db: Session, database_id: uuid.UUID, language: str) -> set[uuid.UUID]:
    """
    Complete the family set of *database_id* for a family tree view.

    Missing roles are created under their reserved name in *language*; a
    missing parents or children property is found through the mirror name of
    the other one. A reserved name another property already uses answers 409.
    The edge roles get timeline and nuance, existing values wrapped into
    timeline slots. Returns the databases whose entry values changed.
    """
    from app.blocks import repository as repo
    from app.blocks import schema_service
    from app.blocks.relation_sync import ensure_bilateral_mirror

    lang = language if language in LANGUAGES else DEFAULT_LANGUAGE
    for role in ROLES:
        schemas = repo.list_schemas(db, database_id)
        if any(role_of(s) == role for s in schemas):
            continue
        counterpart = next(
            (s for s in schemas if role in MIRRORED_ROLES and role_of(s) == MIRRORED_ROLES[role]),
            None,
        )
        if counterpart is not None:
            ensure_bilateral_mirror(
                db, counterpart, database_id, counterpart.config["mirror_property_name"],
            )
            if any(role_of(s) == role for s in repo.list_schemas(db, database_id)):
                continue
        name = reserved_name(role, lang)
        type_, config = _new_config(role, database_id, lang)
        if repo.get_schema_by_name(db, database_id, name) is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"The family tree needs '{name}' as {describe_structure(role)}; "
                    "a property of that name exists with another structure"
                ),
            )
        schema = repo.create_schema(
            db,
            database_id=database_id,
            name=name,
            type=type_,
            position=schema_service.schema_position_after_last(db, database_id),
            config=config,
        )
        if role in MIRRORED_ROLES:
            schema_service.create_eager_mirror(db, schema, config)

    migrated: set[uuid.UUID] = set()
    for role in TREE_EDGE_ROLES:
        schema = next(
            (s for s in repo.list_schemas(db, database_id) if role_of(s) == role), None,
        )
        if schema is None:
            continue
        old_config = dict(schema.config or {})
        new_config = _edges_on(old_config)
        if new_config == old_config:
            continue
        repo.update_schema(db, schema, config=new_config)
        removed = opts.removed_ids(opts.RELATION_TYPE, old_config, new_config)
        if removed and schema_service.purge_options(db, schema, removed):
            migrated.add(database_id)
        migrated |= schema_service.propagate_to_mirror(
            db, schema, schema.name, old_config, name_changed=False, config_changed=True,
        )
        migrated |= schema_service.migrate_values_for_enabled_timeline(
            db, database_id, schema, old_config,
        )
    return migrated
