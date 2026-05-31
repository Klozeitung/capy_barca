"""
Automations router.

HTTP interface for automation CRUD.  All endpoints require a valid session.

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
from app.blocks.router import get_db
from app.session.deps import require_session

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
    _session: str = Depends(require_session),
):
    """
    Return all automations, optionally scoped to a single database.

    Pass ``?database_id=<uuid>`` to retrieve only the automations that
    belong to a specific database block.
    """
    return repo.list_automations(db, database_id=database_id)


@automations_router.get("/{automation_id}", response_model=AutomationResponse)
def get_automation(
    automation_id: uuid.UUID,
    db: Session = Depends(get_db),
    _session: str = Depends(require_session),
):
    automation = repo.get_automation(db, automation_id)
    if automation is None:
        raise HTTPException(status_code=404, detail="Automation not found")
    return automation


@automations_router.post("", response_model=AutomationResponse, status_code=201)
def create_automation(
    payload: AutomationCreate,
    db: Session = Depends(get_db),
    _session: str = Depends(require_session),
):
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
    _session: str = Depends(require_session),
):
    automation = repo.get_automation(db, automation_id)
    if automation is None:
        raise HTTPException(status_code=404, detail="Automation not found")
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
    _session: str = Depends(require_session),
):
    automation = repo.get_automation(db, automation_id)
    if automation is None:
        raise HTTPException(status_code=404, detail="Automation not found")
    repo.delete_automation(db, automation)
    db.commit()


@automations_router.patch(
    "/{automation_id}/toggle", response_model=AutomationResponse
)
def toggle_automation(
    automation_id: uuid.UUID,
    db: Session = Depends(get_db),
    _session: str = Depends(require_session),
):
    """Flip the ``enabled`` flag without requiring the caller to know its current state."""
    automation = repo.get_automation(db, automation_id)
    if automation is None:
        raise HTTPException(status_code=404, detail="Automation not found")
    repo.update_automation(db, automation, enabled=not automation.enabled)
    db.commit()
    db.refresh(automation)
    return automation
