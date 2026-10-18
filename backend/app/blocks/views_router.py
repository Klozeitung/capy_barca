"""
View router.

HTTP interface for database views: listing and editing them, remembering
which view a placement shows, and rendering pages of a view. The logic lives
in ``app.blocks.views``; this module parses requests, enforces access and
shapes responses.

Access
------
Reading a view or a page requires read access to the database and answers 404
otherwise, so the response does not confirm that the database exists. Every
change requires access as well and answers 403, matching the block router.
Views are shared, so anyone who may open the database may arrange its views.

Endpoints
---------
GET    /api/databases/{database_id}/views?placement_id=…
POST   /api/databases/{database_id}/views
POST   /api/databases/{database_id}/views/{view_id}/duplicate
PATCH  /api/databases/{database_id}/views/{view_id}
DELETE /api/databases/{database_id}/views/{view_id}
PUT    /api/databases/{database_id}/views/order
PUT    /api/databases/{database_id}/views/selection
POST   /api/databases/{database_id}/views/{view_id}/entries
"""
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.blocks import views
from app.session.deps import get_current_user, get_db
from app.users.model import User

views_router = APIRouter(prefix="/api/databases", tags=["views"])


# ─── Request / response shapes ───────────────────────────────────────────────


class ViewResponse(BaseModel):
    id: uuid.UUID
    database_id: uuid.UUID
    name: str
    view_type: str
    position: float
    description: Optional[str]
    filter_groups: list[dict]
    sorts: list[dict]
    settings: dict

    model_config = {"from_attributes": True}


class ViewListResponse(BaseModel):
    views: list[ViewResponse]
    selected_view_id: Optional[uuid.UUID]


class ViewCreate(BaseModel):
    name: str = ""
    view_type: str = "table"
    description: Optional[str] = None
    filter_groups: Optional[list[Any]] = None
    sorts: Optional[list[Any]] = None
    settings: Optional[dict] = None


class ViewDuplicate(BaseModel):
    """The copy's name. The frontend builds it, since names are localized."""

    name: str = ""


class ViewUpdate(BaseModel):
    """
    A partial update. Only the fields present in the request are applied.
    ``settings`` is merged key by key; a key sent as ``null`` is removed.
    """

    name: Optional[str] = None
    view_type: Optional[str] = None
    description: Optional[str] = None
    filter_groups: Optional[list[Any]] = None
    sorts: Optional[list[Any]] = None
    settings: Optional[dict[str, Any]] = None


# Fields a partial update may omit but not clear. ``description`` and the
# individual settings keys can be cleared with null.
_NOT_NULLABLE: tuple[str, ...] = ("name", "view_type", "filter_groups", "sorts", "settings")


class ViewOrder(BaseModel):
    view_ids: list[uuid.UUID]


class ViewSelection(BaseModel):
    placement_id: uuid.UUID
    view_id: uuid.UUID


class PageRequest(BaseModel):
    """
    ``limit`` null asks for every entry (up to the server maximum). Views laid
    out on a time axis ignore ``offset`` and ``limit`` and are never paged.
    ``search`` narrows the page by entry name and is not stored.
    """

    offset: int = Field(default=0, ge=0)
    limit: Optional[int] = Field(default=100, ge=1)
    search: str = ""


class EntryPayload(BaseModel):
    id: uuid.UUID
    position: float
    content: Optional[dict]
    icon: Optional[str]
    state: str
    values: dict[str, Optional[dict]]


class RelationTitle(BaseModel):
    id: uuid.UUID
    title: Optional[str]
    database_id: uuid.UUID


class PageResponse(BaseModel):
    """
    ``relations`` maps every linked entry id that resolved to its descriptor;
    an id the entries hold but the map lacks is gone or not readable.
    ``key_values`` maps a key property id to the values linked entries hold
    for it, by entry id.
    """

    entries: list[EntryPayload]
    total: int
    offset: int
    relations: dict[str, RelationTitle]
    key_values: dict[str, dict[str, Any]]


# ─── Endpoints ───────────────────────────────────────────────────────────────


@views_router.get("/{database_id}/views", response_model=ViewListResponse)
def list_views(
    database_id: uuid.UUID,
    placement_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    The views of a database and the one the placement shows.

    *placement_id* defaults to the database itself. The first request for a
    database also creates its system properties and a default view if they
    are missing, which is why this read may write.
    """
    views.require_readable(db, database_id, current_user)
    placement = placement_id or database_id
    if placement != database_id:
        views.require_placement(db, database_id, placement)
    all_views = views.ensure_views(db, database_id)
    db.commit()
    return ViewListResponse(
        views=all_views,
        selected_view_id=views.selected_view_id(db, all_views, placement),
    )


@views_router.post("/{database_id}/views", response_model=ViewResponse, status_code=201)
def create_view(
    database_id: uuid.UUID,
    payload: ViewCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Append a new view. Without settings it gets the default layout."""
    views.require_writable(db, database_id, current_user)
    view = views.create_view(
        db,
        database_id,
        name=payload.name,
        view_type=payload.view_type,
        description=payload.description,
        filter_groups=payload.filter_groups,
        sorts=payload.sorts,
        settings=payload.settings,
    )
    db.commit()
    return view


@views_router.post(
    "/{database_id}/views/{view_id}/duplicate", response_model=ViewResponse, status_code=201,
)
def duplicate_view(
    database_id: uuid.UUID,
    view_id: uuid.UUID,
    payload: ViewDuplicate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Copy a view and place the copy right after the original."""
    views.require_writable(db, database_id, current_user)
    source = views.get_view_or_404(db, database_id, view_id)
    copy = views.duplicate_view(db, database_id, source, name=payload.name)
    db.commit()
    return copy


@views_router.put("/{database_id}/views/order", response_model=list[ViewResponse])
def reorder_views(
    database_id: uuid.UUID,
    payload: ViewOrder,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Put the views in the given order, which must list each exactly once."""
    views.require_writable(db, database_id, current_user)
    ordered = views.reorder_views(db, database_id, payload.view_ids)
    db.commit()
    return ordered


@views_router.put("/{database_id}/views/selection", status_code=204)
def select_view(
    database_id: uuid.UUID,
    payload: ViewSelection,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remember which view a placement shows."""
    views.require_readable(db, database_id, current_user)
    views.select_view(db, database_id, payload.placement_id, payload.view_id)
    db.commit()
    return Response(status_code=204)


@views_router.patch("/{database_id}/views/{view_id}", response_model=ViewResponse)
def update_view(
    database_id: uuid.UUID,
    view_id: uuid.UUID,
    payload: ViewUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Change some fields of a view. The row is locked for the merge, so
    concurrent updates of different fields both survive.
    """
    views.require_writable(db, database_id, current_user)
    changes = {key: getattr(payload, key) for key in payload.model_fields_set}
    not_nullable = [key for key in _NOT_NULLABLE if key in changes and changes[key] is None]
    if not_nullable:
        raise HTTPException(
            status_code=422, detail=f"These fields cannot be null: {', '.join(not_nullable)}",
        )
    view = views.get_view_or_404(db, database_id, view_id, for_update=True)
    views.update_view(db, view, **changes)
    db.commit()
    db.refresh(view)
    return view


@views_router.delete("/{database_id}/views/{view_id}", status_code=204)
def delete_view(
    database_id: uuid.UUID,
    view_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a view. The last view of a database cannot be deleted (409)."""
    views.require_writable(db, database_id, current_user)
    view = views.get_view_or_404(db, database_id, view_id)
    views.delete_view(db, database_id, view)
    db.commit()
    return Response(status_code=204)


@views_router.post("/{database_id}/views/{view_id}/entries", response_model=PageResponse)
def render_page(
    database_id: uuid.UUID,
    view_id: uuid.UUID,
    payload: PageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    One page of a view: its entries with their values, the total number of
    matching entries, and the resolved relations of the page.
    """
    views.require_readable(db, database_id, current_user)
    view = views.get_view_or_404(db, database_id, view_id)
    return views.render_page(
        db,
        view,
        current_user,
        offset=payload.offset,
        limit=payload.limit,
        search=payload.search,
    )
