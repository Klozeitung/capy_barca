"""
Uploaded icons router.

``GET /api/icons`` lists the icons every account may pick; uploading
(``POST``) and deleting (``DELETE /{id}``) are for admins. Each change sends
``custom_icons.changed`` with the full list to every connected account, so
open icon pickers learn about it without reloading. Deleting an icon also
replaces it wherever it was used (``app.icons.service``) and reports the
changed blocks like an icon change made through the block endpoints.
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.blocks.models import Block
from app.icons import service
from app.icons.model import CustomIcon
from app.icons.names import custom_icon_name
from app.session.deps import get_db, require_admin, require_session
from app.users.model import User
from app.ws.broadcaster import emit_block_event, emit_installation_message

icons_router = APIRouter(prefix="/api/icons", tags=["icons"])

CHANGED_MESSAGE = "custom_icons.changed"


class CustomIconResponse(BaseModel):
    """An uploaded icon: ``icon`` is the string stored wherever it is used."""
    id: uuid.UUID
    icon: str
    name: str
    url: str
    created_at: datetime


def _response(icon: CustomIcon) -> CustomIconResponse:
    return CustomIconResponse(
        id=icon.id,
        icon=custom_icon_name(icon.id),
        name=icon.name,
        url=service.icon_url(icon),
        created_at=icon.created_at,
    )


def _listing(db: Session) -> list[CustomIconResponse]:
    return [_response(icon) for icon in service.list_icons(db)]


def _announce(db: Session) -> None:
    """Send the current list to every connected account."""
    emit_installation_message(
        CHANGED_MESSAGE,
        {"icons": [icon.model_dump(mode="json") for icon in _listing(db)]},
        db=db,
    )


def _block_dict(block: Block) -> dict:
    """The block as the block endpoints serialise it in their events."""
    return {
        "id": str(block.id),
        "parent_id": str(block.parent_id) if block.parent_id else None,
        "reference_id": str(block.reference_id) if block.reference_id else None,
        "type": block.type,
        "position": block.position,
        "state": block.state,
        "content": block.content,
        "icon": block.icon,
        "cover": block.cover,
        "owner_id": str(block.owner_id) if getattr(block, "owner_id", None) else None,
    }


@icons_router.get("", response_model=list[CustomIconResponse])
def list_custom_icons(
    _session: uuid.UUID = Depends(require_session),
    db: Session = Depends(get_db),
):
    """Return every uploaded icon, oldest first. Open to every account."""
    return _listing(db)


@icons_router.post("", response_model=CustomIconResponse, status_code=201)
def upload_custom_icon(
    file: UploadFile = File(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Upload an image as a new icon (admins only).

    PNG, JPEG, GIF and WebP up to ``service.MAX_ICON_BYTES``; other types are
    refused with 415, larger files with 413. The icon is named after the file.
    """
    icon = service.store_icon(db, file, admin.id)
    db.commit()
    db.refresh(icon)
    _announce(db)
    return _response(icon)


@icons_router.delete("/{icon_id}", status_code=204)
def delete_custom_icon(
    icon_id: uuid.UUID,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Delete an icon (admins only), replacing it wherever it is used.

    Entries get the default icon of their database, other blocks and database
    defaults lose it; see ``app.icons.service`` for the details.
    """
    icon = db.get(CustomIcon, icon_id)
    if icon is None:
        raise HTTPException(status_code=404, detail="Icon not found")
    try:
        result = service.delete_icon(db, icon)
        db.commit()
    except Exception:
        db.rollback()
        raise
    service.remove_file(icon)

    for block_id, before_icon in result.blocks.items():
        block = db.get(Block, block_id)
        if block is None:
            continue
        after = _block_dict(block)
        emit_block_event(
            db=db,
            event_type="appearance_updated",
            block_id=str(block_id),
            before={**after, "icon": before_icon},
            after=after,
        )
    for database_id in result.databases:
        emit_block_event(
            db=db,
            event_type="database_entries_updated",
            block_id=database_id,
            before=None,
            after={"database_id": database_id},
        )
    _announce(db)
