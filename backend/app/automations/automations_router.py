"""
Automations router.

HTTP interface for automation CRUD.

Authorization
-------------
An automation belongs to a ``database_id``, which is a block. Reaching the
automation therefore means reaching that block, and every endpoint asks
``require_block_access`` about it. Listing filters to the databases the caller
can reach rather than refusing outright, so a member sees their own automations
and nothing else.

The single-item endpoints look the automation up before they can ask the
question at all — the ``database_id`` is only known once the row is loaded — so
an unknown id answers 404 and an unreachable one answers 403.

GET    /api/automations                  list automations (optional ?database_id=)
GET    /api/automations/{id}             get one automation
POST   /api/automations                  create automation
PATCH  /api/automations/{id}             update automation fields
DELETE /api/automations/{id}             delete automation
PATCH  /api/automations/{id}/toggle      flip the enabled flag
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.automations import automations_repository as repo
from app.permissions import repository as perm_repo
from app.session.deps import get_current_user, get_db, require_block_access
from app.users.model import User

automations_router = APIRouter(prefix="/api/automations", tags=["automations"])


# ─── Request / Response schemas ───────────────────────────────────────────────


class AutomationCreate(BaseModel):
    database_id: uuid.UUID
    name: str
    trigger: dict | list  # list for multi-trigger (OR semantics); dict for legacy single-trigger
    actions: list = []
    enabled: bool = True


class AutomationUpdate(BaseModel):
    name: Optional[str] = None
    trigger: Optional[dict | list] = None  # list for multi-trigger; dict for legacy
    actions: Optional[list] = None
    enabled: Optional[bool] = None


class AutomationResponse(BaseModel):
    id: uuid.UUID
    database_id: uuid.UUID
    name: str
    enabled: bool
    trigger: dict | list  # list for multi-trigger (OR semantics); dict for legacy single-trigger
    actions: list

    model_config = {"from_attributes": True}


# ─── Endpoints ────────────────────────────────────────────────────────────────


@automations_router.get("", response_model=list[AutomationResponse])
def list_automations(
    database_id: Optional[uuid.UUID] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return all automations the caller may reach, optionally scoped to one database.

    Pass ``?database_id=<uuid>`` to retrieve only the automations that
    belong to a specific database block; an unreachable one answers 403.
    Without the parameter the result is filtered rather than refused, which is
    the same shape ``list_children`` uses in the block router.
    """
    if database_id is not None:
        require_block_access(db, database_id, current_user)
        return repo.list_automations(db, database_id=database_id)

    return [
        automation
        for automation in repo.list_automations(db)
        if perm_repo.can_user_access(db, automation.database_id, current_user)
    ]


@automations_router.get("/{automation_id}", response_model=AutomationResponse)
def get_automation(
    automation_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    automation = repo.get_automation(db, automation_id)
    if automation is None:
        raise HTTPException(status_code=404, detail="Automation not found")
    require_block_access(db, automation.database_id, current_user)
    return automation


@automations_router.post("", response_model=AutomationResponse, status_code=201)
def create_automation(
    payload: AutomationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_block_access(db, payload.database_id, current_user)
    automation = repo.create_automation(
        db,
        database_id=payload.database_id,
        name=payload.name,
        trigger=payload.trigger,
        actions=payload.actions,
        enabled=payload.enabled,
    )
    db.commit()
    db.refresh(automation)
    return automation


@automations_router.patch("/{automation_id}", response_model=AutomationResponse)
def update_automation(
    automation_id: uuid.UUID,
    payload: AutomationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    automation = repo.get_automation(db, automation_id)
    if automation is None:
        raise HTTPException(status_code=404, detail="Automation not found")
    require_block_access(db, automation.database_id, current_user)
    repo.update_automation(
        db,
        automation,
        name=payload.name,
        trigger=payload.trigger,
        actions=payload.actions,
        enabled=payload.enabled,
    )
    db.commit()
    db.refresh(automation)
    return automation


@automations_router.delete("/{automation_id}", status_code=204)
def delete_automation(
    automation_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    automation = repo.get_automation(db, automation_id)
    if automation is None:
        raise HTTPException(status_code=404, detail="Automation not found")
    require_block_access(db, automation.database_id, current_user)
    repo.delete_automation(db, automation)
    db.commit()


@automations_router.patch(
    "/{automation_id}/toggle", response_model=AutomationResponse
)
def toggle_automation(
    automation_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Flip the ``enabled`` flag without requiring the caller to know its current state."""
    automation = repo.get_automation(db, automation_id)
    if automation is None:
        raise HTTPException(status_code=404, detail="Automation not found")
    require_block_access(db, automation.database_id, current_user)
    repo.update_automation(db, automation, enabled=not automation.enabled)
    db.commit()
    db.refresh(automation)
    return automation
