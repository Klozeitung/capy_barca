"""
Permissions router.

Exposes two endpoints for reading and writing the permission configuration
of a single block:

GET  /api/blocks/{block_id}/permissions
    Any authenticated user with read access to the block; 404 otherwise, the
    same answer as for a block that does not exist. Returns the block's own
    mode, the level for everyone, the resolved effective mode, the owner
    UUID, and the grant list with one access level per account.

PUT  /api/blocks/{block_id}/permissions
    Owner or admin only. Replaces the mode, the level for everyone and the
    grant list atomically. Sending mode='inherit' removes the explicit row
    entirely (reverts to inheriting from the parent).

Access levels
-------------
'read' lets an account see the block and comment on it; 'write' also lets it
change the block. The owner and every admin always have write access. The
level for everyone only applies while the mode is 'everyone'; grants only
apply while the mode is 'whitelist'.
"""
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.blocks import repository as block_repo
from app.blocks.models import Block
from app.blocks.router import get_db
from app.permissions import repository as perm_repo
from app.permissions.model import READ, WRITE, BlockPermission
from app.session.deps import get_current_user
from app.users.model import User

permissions_router = APIRouter(prefix="/api/blocks", tags=["permissions"])

_VALID_MODES = frozenset({"everyone", "inherit", "private", "whitelist"})

Level = Literal["read", "write"]


# ─── Schemas ──────────────────────────────────────────────────────────────────


class GrantEntry(BaseModel):
    user_id: uuid.UUID
    level: Level = WRITE


class PermissionResponse(BaseModel):
    block_id: uuid.UUID
    mode: str
    # Level every account gets while the mode is 'everyone'. Reported as
    # 'write' for a block without its own row.
    everyone_level: Level
    owner_id: Optional[uuid.UUID]
    grants: list[GrantEntry]
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
    everyone_level: Level = WRITE
    grants: list[GrantEntry] = []


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _may_edit(block: Block, user: User) -> bool:
    """Owner or admin."""
    return user.role == "admin" or (
        block.owner_id is not None and block.owner_id == user.id
    )


def _grant_entries(perm_row: Optional[BlockPermission]) -> list[GrantEntry]:
    if perm_row is None:
        return []
    return [GrantEntry(user_id=g.user_id, level=g.level) for g in perm_row.grants]


# ─── Endpoints ────────────────────────────────────────────────────────────────


@permissions_router.get(
    "/{block_id}/permissions", response_model=PermissionResponse
)
def get_permission(
    block_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return the permission configuration for a block.

    Needs read access: the configuration names the accounts the block is
    shared with, which is not for callers who cannot see the block.
    """
    block = block_repo.get_block(db, block_id)
    if block is None or not perm_repo.can_user_access(db, block_id, current_user, level=READ):
        raise HTTPException(
            status_code=404, detail=f"Block {block_id} not found"
        )

    perm_row = perm_repo.get_permission_row(db, block_id)
    mode = perm_row.mode if perm_row is not None else "inherit"
    everyone_level = perm_row.everyone_level if perm_row is not None else WRITE

    effective_mode, _, _ = perm_repo.resolve_effective_permission(db, block_id)

    # When mode is 'inherit', point to the ancestor that provides the setting.
    inherited_from_id: Optional[uuid.UUID] = None
    if mode == "inherit" and block.parent_id is not None:
        inherited_from_id = perm_repo.find_permission_source(
            db, block.parent_id
        )

    warn_accessibility = (
        mode != "inherit"
        and perm_repo.is_more_permissive_than_parent_chain(db, block_id, mode)
    )

    return PermissionResponse(
        block_id=block_id,
        mode=mode,
        everyone_level=everyone_level,
        owner_id=block.owner_id,
        grants=_grant_entries(perm_row),
        effective_mode=effective_mode,
        inherited_from_id=inherited_from_id,
        can_edit=_may_edit(block, current_user),
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
    Set the permission mode, the level for everyone and the grant list for a
    block.

    Only the block owner or an admin may change permissions.
    Sending ``mode='inherit'`` removes the explicit permission row so the
    block inherits from its parent again. An account listed more than once
    in ``grants`` gets the level of its last entry.
    """
    block = block_repo.get_block(db, block_id)
    if block is None:
        raise HTTPException(
            status_code=404, detail=f"Block {block_id} not found"
        )

    if not _may_edit(block, current_user):
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
        return PermissionResponse(
            block_id=block_id,
            mode="inherit",
            everyone_level=WRITE,
            owner_id=block.owner_id,
            grants=[],
            effective_mode=effective_mode,
            inherited_from_id=inherited_from_id,
            can_edit=_may_edit(block, current_user),
            warn_accessibility=False,  # inherit is never more permissive
        )

    perm_row = perm_repo.set_permission(
        db,
        block_id,
        payload.mode,
        {grant.user_id: grant.level for grant in payload.grants},
        everyone_level=payload.everyone_level,
    )
    db.commit()
    db.refresh(perm_row)

    effective_mode, _, _ = perm_repo.resolve_effective_permission(db, block_id)
    _warn = perm_repo.is_more_permissive_than_parent_chain(db, block_id, perm_row.mode)
    return PermissionResponse(
        block_id=block_id,
        mode=perm_row.mode,
        everyone_level=perm_row.everyone_level,
        owner_id=block.owner_id,
        grants=_grant_entries(perm_row),
        effective_mode=effective_mode,
        inherited_from_id=None,
        can_edit=_may_edit(block, current_user),
        warn_accessibility=_warn,
    )
