"""
Family tree router.

Endpoints
---------
POST /api/databases/{database_id}/views/{view_id}/family-tree       the tree
POST /api/databases/{database_id}/views/{view_id}/family-tree/map   run the sibling mapper

Both need a view of type ``family_tree`` (404 otherwise). Drawing needs read
access to the database (404 otherwise, see ``app.blocks.views_router``),
mapping write access (403). Mapping needs a root (422). Nothing is cached:
the tree is computed from the current data on every request, and the mapper
writes only when it is run.

Both take the family tree settings the client shows (``settings``, the
``familyTree*`` keys of the view settings) and work with them instead of the
stored ones. A change made in the view settings is drawn at once, without
waiting for it to be saved; nothing is stored here. Without ``settings`` the
stored ones apply. Keys outside the family tree, or malformed values,
answer 422.
"""
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.blocks import kinship, relation_lock, views
from app.blocks.database_events import emit_entries_updated_for
from app.family_tree import render as tree_render
from app.family_tree import siblings
from app.family_tree.graph import DEFAULT_SCOPE, SCOPES, load_graph, scope
from app.session.deps import get_current_user, get_db
from app.users.model import User

family_tree_router = APIRouter(prefix="/api/databases", tags=["family-tree"])


# ─── Request and response shapes ─────────────────────────────────────────────

#: The keys of the view settings a family tree reads.
FAMILY_TREE_KEYS = (
    "familyTreeRootId", "familyTreeAt", "familyTreeScope",
    "familyTreeProperties", "familyTreeOverlays",
)


class TreeRequest(BaseModel):
    """The family tree settings to work with instead of the stored ones."""

    settings: Optional[dict[str, Any]] = None



class TreeSegment(BaseModel):
    start: Optional[str]
    end: Optional[str]
    nuance: Optional[str] = None
    base: Optional[str] = None
    label: Optional[str] = None


class TreeProperty(BaseModel):
    schema_id: str
    name: str
    tokens: Optional[list[dict[str, Any]]] = None
    slots: Optional[list[dict[str, Any]]] = None


class TreeNode(BaseModel):
    id: str
    title: Optional[str]
    icon: Optional[str]
    avatar: Optional[str]
    generation: Optional[int]
    outsider: bool = False
    virtual: bool = False
    birth: Optional[str]
    death: Optional[str]
    birth_value: Optional[str]
    death_value: Optional[str]
    sex: Optional[str]
    warnings: list[str]
    properties: list[TreeProperty]


class ParentEdge(BaseModel):
    parent: str
    child: str
    anchor: bool
    broken: bool
    segments: list[TreeSegment]


class PartnerEdge(BaseModel):
    a: str
    b: str
    segments: list[TreeSegment]


class VirtualEdge(BaseModel):
    parent: str
    child: str


class OverlayEdge(BaseModel):
    source: str
    target: str
    segments: list[TreeSegment]


class Overlay(BaseModel):
    schema_id: Optional[str]
    name: Optional[str]
    valid: bool
    direction: str
    color: Optional[str]
    show_outsiders: bool
    show_nuances: bool
    edges: list[OverlayEdge]


class TreeResponse(BaseModel):
    root: Optional[str]
    root_valid: bool
    scope: str
    nodes: list[TreeNode]
    parent_edges: list[ParentEdge]
    partner_edges: list[PartnerEdge]
    virtual_edges: list[VirtualEdge]
    overlays: list[Overlay]


class Finding(BaseModel):
    kind: str
    entries: list[str]


class EntryRef(BaseModel):
    title: Optional[str]
    icon: Optional[str]


class MapResponse(BaseModel):
    """
    ``conflicts`` and ``warnings`` name entries by id; ``entries`` gives the
    title and icon of each, for the list the frontend shows.
    """

    added: int
    updated: int
    removed: int
    filled: int
    conflicts: list[Finding]
    warnings: list[Finding]
    entries: dict[str, EntryRef]


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _family_tree_view(db: Session, database_id: uuid.UUID, view_id: uuid.UUID, *, for_update=False):
    view = views.get_view_or_404(db, database_id, view_id, for_update=for_update)
    if view.view_type != kinship.FAMILY_TREE_VIEW:
        raise HTTPException(status_code=404, detail="The view is not a family tree")
    return view


def _language(user: User, request: Request) -> str:
    return kinship.language_for(user.language, request.headers.get("accept-language"))


def _settings(view, payload: Optional[TreeRequest]) -> dict:
    """
    The settings to work with: the view's stored ones, with the family tree
    keys replaced by those of *payload* when it carries any. Raises 422.
    """
    stored = dict(view.settings or {})
    if payload is None or payload.settings is None:
        return stored
    foreign = sorted(k for k in payload.settings if k not in FAMILY_TREE_KEYS)
    if foreign:
        raise HTTPException(status_code=422, detail=f"Not family tree settings: {', '.join(foreign)}")
    try:
        validated = views.ViewSettings.model_validate(payload.settings).model_dump(exclude_none=True)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False)) from exc
    kept = {k: v for k, v in stored.items() if k not in FAMILY_TREE_KEYS}
    return {**kept, **validated}


# ─── Endpoints ───────────────────────────────────────────────────────────────


@family_tree_router.post(
    "/{database_id}/views/{view_id}/family-tree", response_model=TreeResponse,
)
def get_family_tree(
    database_id: uuid.UUID,
    view_id: uuid.UUID,
    request: Request,
    payload: Optional[TreeRequest] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The tree as the settings describe it, for every point in time."""
    views.require_readable(db, database_id, current_user)
    view = _family_tree_view(db, database_id, view_id)
    settings = _settings(view, payload)
    return tree_render.render(db, view, current_user, _language(current_user, request), settings)


@family_tree_router.post(
    "/{database_id}/views/{view_id}/family-tree/map", response_model=MapResponse,
)
def map_family_tree(
    database_id: uuid.UUID,
    view_id: uuid.UUID,
    payload: Optional[TreeRequest] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Derive the siblings of the people in the tree's scope and write them
    (see ``app.family_tree.siblings``). Contradictions do not stop the run;
    they come back with the result. Broadcasts ``database_entries_updated``
    for the database and every database whose computed values changed.
    """
    views.require_writable(db, database_id, current_user)
    view = _family_tree_view(db, database_id, view_id)
    settings = _settings(view, payload)
    root = settings.get("familyTreeRootId")
    if not root:
        raise HTTPException(status_code=422, detail="The family tree has no root")
    mode = settings.get("familyTreeScope") if settings.get("familyTreeScope") in SCOPES else DEFAULT_SCOPE

    relation_lock.lock_relation_databases(db, [database_id])
    graph = load_graph(db, database_id)
    if root not in graph.people:
        raise HTTPException(status_code=422, detail="The root of the family tree does not exist")
    try:
        result = siblings.run(db, graph, scope(graph, root, mode), root, current_user.id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    if result.changed_entries:
        emit_entries_updated_for(db, [database_id, *sorted(result.affected_databases, key=str)])

    named = {e for f in (*result.conflicts, *result.warnings) for e in f["entries"]}
    return MapResponse(
        added=result.added,
        updated=result.updated,
        removed=result.removed,
        filled=result.filled,
        conflicts=result.conflicts,
        warnings=result.warnings,
        entries={
            e: EntryRef(title=graph.people[e].title, icon=graph.people[e].icon)
            for e in named if e in graph.people
        },
    )
