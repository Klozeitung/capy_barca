"""
What the family tree view draws, independent of the point in time.

:func:`render` reads the view's settings (root, scope, the properties under
the life span, the overlays) and returns every person, link and overlay with
the periods they hold. The frontend picks what holds at the chosen instant
(``familyTreeAt``, later a slider) and lays out the rows; nothing here needs
to be asked again when the instant changes.

Periods
-------
Every segment carries ``start`` and ``end`` as wall-clock minutes (see
``app.family_tree.graph``), None for an open side, ``end`` inclusive. They
are the stored periods with the dates of birth and death applied, which are
never written into a relation:

* A biological parent link without a start begins at the child's birth.
* Foster and step parenthood and every partnership end at the earliest death
  of the two; biological, putative, legal, adoptive and unspecified
  parenthood do not.

Property line
-------------
Each property under the life span comes as tokens: ``{"text": …}``,
``{"date": …, "end": …}`` (formatted by the frontend in the user's date
format) or ``{"check": bool}``. A value with a timeline comes as ``slots``
with the period of each; rollups and formulas show their stored result.
Linked entries in a database the user may not read do not resolve.
"""
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import timezones
from app.blocks import kinship
from app.blocks import options as opts
from app.blocks import repository as repo
from app.blocks import resolution
from app.blocks.models import Block, PropertyValue
from app.blocks.timeline import parse_pool_range
from app.family_tree.generations import place
from app.family_tree.graph import (
    DEFAULT_SCOPE,
    SCOPES,
    FamilyGraph,
    Segment,
    segments_of,
    earliest,
    load_graph,
    minute,
    scope,
)

_DATE_TEXT = re.compile(r"^\d{4}-\d{2}-\d{2}")

ENDS_WITH_DEATH = frozenset({"kin.parent.foster", "kin.parent.step"})

OVERLAY_DIRECTIONS = ("forward", "both", "none")


# ─── Segments ────────────────────────────────────────────────────────────────


def _segment_dict(segment: Segment, start, end, labels: dict[str, str]) -> dict:
    return {
        "start": start,
        "end": end,
        "nuance": segment.nuance,
        "base": segment.base,
        "label": labels.get(segment.nuance) if segment.nuance else None,
    }


def parent_segments(graph: FamilyGraph, parent: str, child: str, labels: dict[str, str]) -> list[dict]:
    """The periods of a parent link with births and deaths applied."""
    result = []
    birth = graph.people[child].birth
    deaths = (graph.people[parent].death, graph.people[child].death)
    for segment in graph.people[child].parents[parent]:
        start, end = segment.start, segment.end
        if start is None and segment.base == kinship.PARENT_BIOLOGICAL:
            start = birth
        if segment.base in ENDS_WITH_DEATH:
            end = earliest(end, *deaths)
        result.append(_segment_dict(segment, start, end, labels))
    return result


def partner_segments(graph: FamilyGraph, a: str, b: str, labels: dict[str, str]) -> list[dict]:
    """The periods of a partnership, ending at the earlier death."""
    deaths = (graph.people[a].death, graph.people[b].death)
    return [
        _segment_dict(s, s.start, earliest(s.end, *deaths), labels)
        for s in graph.people[a].partners[b]
    ]


# ─── Property line ───────────────────────────────────────────────────────────


def _number(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _result_tokens(result: Any, titles: dict[str, str]) -> list[dict]:
    """Tokens of a stored formula or rollup result."""
    if result is None or result == "":
        return []
    if isinstance(result, bool):
        return [{"check": result}]
    if isinstance(result, (int, float)):
        return [{"text": _number(result)}]
    if isinstance(result, str):
        if _DATE_TEXT.match(result) and minute(result):
            return [{"date": result, "end": None}]
        return [{"text": result}]
    if isinstance(result, dict):
        if "start" in result or "end" in result:
            return [{"date": result.get("start"), "end": result.get("end")}] if result.get("start") else []
        if "id" in result:
            title = titles.get(str(result["id"])) or result.get("title")
            return [{"text": title}] if title else []
        if "label" in result:
            return [{"text": str(result["label"])}]
        return []
    if isinstance(result, list):
        tokens = []
        for item in result:
            tokens += _result_tokens(item, titles)
        return tokens
    return [{"text": str(result)}]


def _plain_tokens(schema: Any, value: Any, titles: dict[str, str], zone) -> list[dict]:
    """Tokens of one value without timeline (or of one slot)."""
    if not isinstance(value, dict):
        return []
    type_ = schema.type
    if type_ in ("formula", "rollup"):
        return _result_tokens(value.get("result"), titles)
    if type_ in ("text", "email", "phone", "url"):
        text = value.get("text") if "text" in value else value.get("value")
        return [{"text": text}] if isinstance(text, str) and text else []
    if type_ == "number":
        number = value.get("number")
        return [] if number is None else [{"text": _number(number)}]
    if type_ == "checkbox":
        return [{"check": bool(value.get("checked"))}]
    if type_ == "select":
        labels = opts.label_map(opts.option_list("select", schema.config))
        ids = value.get("options") if isinstance(value.get("options"), list) else [value.get("option")]
        return [{"text": labels.get(i, i)} for i in ids if isinstance(i, str) and labels.get(i, i)]
    if type_ == "date":
        local = timezones.date_value_to_local(value, zone)
        start = local.get("start") if isinstance(local, dict) else None
        return [{"date": start, "end": local.get("end")}] if start else []
    if type_ in ("relation", "parent_item", "sub_item"):
        return [
            {"text": titles[i]} for i in value.get("related_ids") or []
            if isinstance(i, str) and titles.get(i)
        ]
    if type_ in ("created_time", "last_edited_time"):
        moment = value.get("datetime")
        return [{"date": timezones.time_to_local(moment, zone), "end": None}] if moment else []
    return []


def property_entry(schema: Any, value: Any, titles: dict[str, str], zone) -> dict:
    """One property of the line under a person's life span."""
    entry: dict = {"schema_id": str(schema.id), "name": schema.name}
    if isinstance(value, dict) and isinstance(value.get("_timeline"), dict):
        slots = []
        for key, slot in value["_timeline"].items():
            start, end = parse_pool_range(key)
            tokens = _plain_tokens(schema, slot, titles, zone)
            if tokens:
                slots.append({"start": minute(start), "end": minute(end), "tokens": tokens})
        slots.sort(key=lambda s: s["start"] or "")
        entry["slots"] = slots
    else:
        entry["tokens"] = _plain_tokens(schema, value, titles, zone)
    return entry


# ─── Rendering ───────────────────────────────────────────────────────────────


def _values(db: Session, schema_ids: list[uuid.UUID], database_id: uuid.UUID) -> dict[tuple[str, str], Any]:
    if not schema_ids:
        return {}
    rows = db.execute(
        select(PropertyValue.page_id, PropertyValue.property_schema_id, PropertyValue.value)
        .join(Block, Block.id == PropertyValue.page_id)
        .where(Block.parent_id == database_id, PropertyValue.property_schema_id.in_(schema_ids))
    ).all()
    return {(str(page), str(schema)): value for page, schema, value in rows}


def overlay_schema_valid(schema: Any, database_id: uuid.UUID) -> bool:
    """Whether *schema* can be an overlay: a two-way relation within the database, not of the set."""
    if schema is None or schema.database_id != database_id or schema.type != "relation":
        return False
    config = schema.config or {}
    return (
        str(config.get("target_database_id") or "") == str(database_id)
        and config.get("direction") in ("bilateral", "bilateral_self")
        and kinship.role_of(schema) is None
    )


def _overlays(
    db: Session, graph: FamilyGraph, members: set[str], settings: list[dict],
) -> tuple[list[dict], set[str]]:
    """The overlays and the outsiders they bring in."""
    database_id = graph.database_id
    schemas = {str(s.id): s for s in repo.list_schemas(db, database_id)}
    valid = [
        schemas[o["schemaId"]] for o in settings
        if overlay_schema_valid(schemas.get(o.get("schemaId")), database_id)
    ]
    values = _values(db, [s.id for s in valid], database_id)
    outsiders: set[str] = set()
    result = []
    for overlay in settings:
        schema = schemas.get(overlay.get("schemaId"))
        entry = {
            "schema_id": overlay.get("schemaId"),
            "name": schema.name if schema is not None else None,
            "valid": overlay_schema_valid(schema, database_id),
            "direction": overlay.get("direction") or "forward",
            "color": overlay.get("color"),
            "show_outsiders": bool(overlay.get("showOutsiders")),
            "show_nuances": bool(overlay.get("showNuances")),
            "edges": [],
        }
        result.append(entry)
        if not entry["valid"]:
            continue
        labels = {
            o["id"]: o.get("label")
            for o in (schema.config or {}).get("nuance", {}).get("options") or []
            if isinstance(o, dict) and "id" in o
        }
        symmetric = (schema.config or {}).get("direction") == "bilateral_self"
        seen: set = set()
        for source in graph.people:
            for target, segments in segments_of(values.get((source, str(schema.id))), []).items():
                if target not in graph.people or target == source:
                    continue
                inside = (source in members) + (target in members)
                if inside == 0 or (inside == 1 and not entry["show_outsiders"]):
                    continue
                key = frozenset((source, target)) if symmetric else (source, target)
                if key in seen:
                    continue
                seen.add(key)
                outsiders |= {source, target} - members
                entry["edges"].append({
                    "source": source,
                    "target": target,
                    "segments": [
                        {"start": s.start, "end": s.end, "label": labels.get(s.nuance) if s.nuance else None}
                        for s in segments
                    ],
                })
    return result, outsiders


def render(db: Session, view: Any, user: Any, language: str) -> dict:
    """The family tree of *view* (see the module docstring)."""
    database_id = view.database_id
    settings = view.settings or {}
    root = settings.get("familyTreeRootId")
    mode = settings.get("familyTreeScope") if settings.get("familyTreeScope") in SCOPES else DEFAULT_SCOPE
    graph = load_graph(db, database_id)
    empty = {
        "root": root, "root_valid": False, "scope": mode, "nodes": [], "parent_edges": [],
        "partner_edges": [], "virtual_edges": [], "overlays": [],
    }
    if not root or root not in graph.people:
        return empty

    members = scope(graph, root, mode)
    placement = place(graph, root, members)
    catalogue = kinship.catalogue(graph.aliases, language)
    labels = {o["id"]: o["label"] for options in catalogue.values() for o in options}

    overlays, outsiders = _overlays(db, graph, members, settings.get("familyTreeOverlays") or [])
    shown = members | outsiders

    property_ids = [p for p in settings.get("familyTreeProperties") or [] if isinstance(p, str)]
    schemas = {str(s.id): s for s in repo.list_schemas(db, database_id)}
    line_schemas = [schemas[p] for p in property_ids if p in schemas]
    line_values = _values(db, [s.id for s in line_schemas], database_id)
    resolved = resolution.resolve_relations(
        db,
        line_schemas,
        [{str(s.id): line_values.get((person, str(s.id))) for s in line_schemas} for person in shown],
        user,
    )
    titles = {key: t.title for key, t in resolved.titles.items() if t.title}
    zone = timezones.zone_of(db)

    nodes = []
    for person_id in sorted(shown, key=graph.order_key):
        person = graph.people[person_id]
        nodes.append({
            "id": person_id,
            "title": person.title,
            "icon": person.icon,
            "avatar": person.avatar,
            "generation": placement.generation.get(person_id),
            "outsider": person_id not in members,
            "birth": person.birth,
            "death": person.death,
            "birth_value": person.birth_value,
            "death_value": person.death_value,
            "sex": person.sex,
            "warnings": placement.warnings.get(person_id, []),
            "properties": [
                property_entry(s, line_values.get((person_id, str(s.id))), titles, zone)
                for s in line_schemas
            ],
        })
    for virtual in placement.virtual:
        nodes.append({
            "id": virtual.id, "virtual": True, "generation": virtual.generation,
            "title": None, "icon": None, "avatar": None, "outsider": False,
            "birth": None, "death": None, "birth_value": None, "death_value": None,
            "sex": None, "warnings": [], "properties": [],
        })

    parent_edges = []
    for child in sorted(members, key=graph.order_key):
        for parent in graph.people[child].parents:
            if parent not in members:
                continue
            parent_edges.append({
                "parent": parent,
                "child": child,
                "anchor": placement.anchor.get(child) == parent,
                "broken": (parent, child) in placement.broken,
                "segments": parent_segments(graph, parent, child, labels),
            })

    partner_edges = []
    seen: set[frozenset] = set()
    for person_id in sorted(members, key=graph.order_key):
        for other in graph.people[person_id].partners:
            pair = frozenset((person_id, other))
            if other not in members or other == person_id or pair in seen:
                continue
            seen.add(pair)
            partner_edges.append({
                "a": person_id, "b": other,
                "segments": partner_segments(graph, person_id, other, labels),
            })

    virtual_edges = [
        {"parent": v.id, "child": child} for v in placement.virtual for child in v.children
    ]
    return {
        "root": root,
        "root_valid": True,
        "scope": mode,
        "nodes": nodes,
        "parent_edges": parent_edges,
        "partner_edges": partner_edges,
        "virtual_edges": virtual_edges,
        "overlays": overlays,
    }
