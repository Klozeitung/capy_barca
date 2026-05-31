"""
Automation repository.

All database access for the automations module lives here.
No business logic — only queries and mutations.
"""
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.automations.automations_models import Automation


# ─── Read ─────────────────────────────────────────────────────────────────────


def list_automations(
    db: Session,
    database_id: Optional[uuid.UUID] = None,
) -> list[Automation]:
    """Return all automations, optionally filtered by owning database."""
    q = db.query(Automation)
    if database_id is not None:
        q = q.filter(Automation.database_id == database_id)
    return q.order_by(Automation.created_at).all()


def list_enabled_for_database(
    db: Session,
    database_id: uuid.UUID,
) -> list[Automation]:
    """
    Return all enabled automations owned by *database_id*.

    Used by the engine's query layer as the SQL pre-filter step.  The
    Python fine-matcher that follows evaluates the full trigger JSON
    (including wildcard and negation fields) against the event.

    Note: automations whose trigger.db_uuid is "" (wildcard) match any
    property within the owning database.  Cross-database global matching
    (trigger.db_uuid = "" firing for unrelated databases) is not supported
    in this MVP; a future revision may add a nullable database_id sentinel
    for workspace-scoped automations.
    """
    return (
        db.query(Automation)
        .filter(
            Automation.database_id == database_id,
            Automation.enabled.is_(True),
        )
        .all()
    )


def get_automation(
    db: Session,
    automation_id: uuid.UUID,
) -> Optional[Automation]:
    return db.get(Automation, automation_id)


# ─── Write ────────────────────────────────────────────────────────────────────


def create_automation(
    db: Session,
    *,
    database_id: uuid.UUID,
    name: str,
    trigger: dict,
    actions: list,
    enabled: bool = True,
) -> Automation:
    automation = Automation(
        database_id=database_id,
        name=name,
        enabled=enabled,
        trigger=trigger,
        actions=actions,
    )
    db.add(automation)
    db.flush()
    return automation


def update_automation(
    db: Session,
    automation: Automation,
    *,
    name: Optional[str] = None,
    trigger: Optional[dict] = None,
    actions: Optional[list] = None,
    enabled: Optional[bool] = None,
) -> Automation:
    if name is not None:
        automation.name = name
    if trigger is not None:
        automation.trigger = trigger
    if actions is not None:
        automation.actions = actions
    if enabled is not None:
        automation.enabled = enabled
    db.flush()
    return automation


def delete_automation(db: Session, automation: Automation) -> None:
    db.delete(automation)
    db.flush()
