"""
Permissions router.

Exposes two endpoints for reading and writing the permission configuration
of a single block:

GET  /api/blocks/{block_id}/permissions
    Any authenticated user.  Returns the block's own mode, the resolved
    effective mode, the owner UUID, and the grant list.

PUT  /api/blocks/{block_id}/permissions
    Owner or admin only.  Replaces the mode and grant list atomically.
    Sending mode='inherit' removes the explicit row entirely (reverts to
    inheriting from the parent).
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.blocks import repository as block_repo
from app.blocks.router import get_db
from app.permissions import repository as perm_repo
from app.session.deps import get_current_user
from app.users.model import User

permissions_router = APIRouter(prefix="/api/blocks", tags=["permissions"])

_VALID_MODES = frozenset({"everyone", "inherit", "private", "whitelist"})


# ─── Schemas ──────────────────────────────────────────────────────────────────


class PermissionResponse(BaseModel):
    block_id: uuid.UUID
    mode: str
    owner_id: Optional[uuid.UUID]
    grants: list[uuid.UUID]
    effective_mode: str
    inherited_from_id: Optional[uuid.UUID]
    # Whether the requesting user may modify this block's permissions.
    # Computed server-side so the frontend never needs to compare user IDs.
    can_edit: bool
    # True when this block's explicit mode is more permissive than its nearest
    # ancestor with an explicit row.  The block is then only reachable via a
    # direct link because ancestor navigation is blocked.
    warn_accessibility: bool


class PermissionUpdate(BaseModel):
    mode: str
    grants: list[uuid.UUID] = []


# ─── Endpoints ────────────────────────────────────────────────────────────────


@permissions_router.get(
    "/{block_id}/permissions", response_model=PermissionResponse
)
def get_permission(
    block_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the permission configuration for a block."""
    block = block_repo.get_block(db, block_id)
    if block is None:
        raise HTTPException(
            status_code=404, detail=f"Block {block_id} not found"
        )

    perm_row = perm_repo.get_permission_row(db, block_id)
    mode = perm_row.mode if perm_row is not None else "inherit"
    grants = [g.user_id for g in perm_row.grants] if perm_row is not None else []

    effective_mode, _, _ = perm_repo.resolve_effective_permission(db, block_id)

    # When mode is 'inherit', point to the ancestor that provides the setting.
    inherited_from_id: Optional[uuid.UUID] = None
    if mode == "inherit" and block.parent_id is not None:
        inherited_from_id = perm_repo.find_permission_source(
            db, block.parent_id
        )

    can_edit = (
        current_user.role == "admin"
        or (block.owner_id is not None and block.owner_id == current_user.id)
    )
    warn_accessibility = (
        mode != "inherit"
        and perm_repo.is_more_permissive_than_parent_chain(db, block_id, mode)
    )

    return PermissionResponse(
        block_id=block_id,
        mode=mode,
        owner_id=block.owner_id,
        grants=grants,
        effective_mode=effective_mode,
        inherited_from_id=inherited_from_id,
        can_edit=can_edit,
        warn_accessibility=warn_accessibility,
    )


@permissions_router.put(
    "/{block_id}/permissions", response_model=PermissionResponse
)
def set_permission(
    block_id: uuid.UUID,
    payload: PermissionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Set the permission mode and grant list for a block.

    Only the block owner or an admin may change permissions.
    Sending ``mode='inherit'`` removes the explicit permission row so the
    block inherits from its parent again.
    """
    block = block_repo.get_block(db, block_id)
    if block is None:
        raise HTTPException(
            status_code=404, detail=f"Block {block_id} not found"
        )

    if current_user.role != "admin" and block.owner_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Only the block owner or an admin may change permissions.",
        )

    if payload.mode not in _VALID_MODES:
        raise HTTPException(
            status_code=422, detail=f"Invalid permission mode: {payload.mode!r}"
        )

    if payload.mode == "inherit":
        perm_repo.delete_permission(db, block_id)
        db.commit()
        effective_mode, _, _ = perm_repo.resolve_effective_permission(
            db, block_id
        )
        inherited_from_id = (
            perm_repo.find_permission_source(db, block.parent_id)
            if block.parent_id
            else None
        )
        _can_edit = (
            current_user.role == "admin"
            or (block.owner_id is not None and block.owner_id == current_user.id)
        )
        return PermissionResponse(
            block_id=block_id,
            mode="inherit",
            owner_id=block.owner_id,
            grants=[],
            effective_mode=effective_mode,
            inherited_from_id=inherited_from_id,
            can_edit=_can_edit,
            warn_accessibility=False,  # inherit is never more permissive
        )

    perm_row = perm_repo.set_permission(db, block_id, payload.mode, payload.grants)
    db.commit()
    db.refresh(perm_row)

    effective_mode, _, _ = perm_repo.resolve_effective_permission(db, block_id)
    _can_edit = (
        current_user.role == "admin"
        or (block.owner_id is not None and block.owner_id == current_user.id)
    )
    _warn = perm_repo.is_more_permissive_than_parent_chain(db, block_id, perm_row.mode)
    return PermissionResponse(
        block_id=block_id,
        mode=perm_row.mode,
        owner_id=block.owner_id,
        grants=[g.user_id for g in perm_row.grants],
        effective_mode=effective_mode,
        inherited_from_id=None,
        can_edit=_can_edit,
        warn_accessibility=_warn,
    )
