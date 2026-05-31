"""
User management router.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.session.deps import get_current_user, get_db, require_admin, require_session
from app.users import repository as user_repo
from app.users.model import User

users_router = APIRouter(prefix="/api/users", tags=["users"])


# ─── Schemas ──────────────────────────────────────────────────────────────────


class UserNameResponse(BaseModel):
    id: uuid.UUID
    username: str


class UserResponse(BaseModel):
    id: uuid.UUID
    username: str
    role: str
    is_active: bool

    model_config = {"from_attributes": True}


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=8)
    role: str = Field(default="member", pattern="^(admin|member)$")


class ChangeUsernameRequest(BaseModel):
    username: str = Field(min_length=1)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class AdminResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8)


class ChangeRoleRequest(BaseModel):
    role: str = Field(pattern="^(admin|member)$")


# ─── Own profile ──────────────────────────────────────────────────────────────


@users_router.get("/names", response_model=list[UserNameResponse])
def list_user_names(
    _session: uuid.UUID = Depends(require_session),
    db: Session = Depends(get_db),
):
    """
    Return id + username for all active users.

    Available to every authenticated user (not admin-only) so that
    ``created_by`` / ``last_edited_by`` database cells can resolve UUIDs
    to display names for all roles.
    """
    users = db.query(User).filter(User.is_active.is_(True)).order_by(User.username).all()
    return [UserNameResponse(id=u.id, username=u.username) for u in users]


@users_router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    """Return the profile of the currently authenticated user."""
    return current_user


@users_router.patch("/me", response_model=UserResponse)
def change_own_username(
    payload: ChangeUsernameRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Change the username of the currently authenticated user. Returns 409 if taken."""
    if payload.username != current_user.username:
        if user_repo.get_by_username(db, payload.username) is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Benutzername '{payload.username}' ist bereits vergeben",
            )
        user_repo.update_username(db, current_user, payload.username)
        db.commit()
        db.refresh(current_user)
    return current_user


@users_router.patch("/me/password", status_code=204)
def change_own_password(
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Change own password. Requires the correct current password."""
    if not user_repo.verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(status_code=401, detail="Aktuelles Passwort ist falsch")
    user_repo.update_password(db, current_user, payload.new_password)
    db.commit()


# ─── Admin: user list + create ────────────────────────────────────────────────


@users_router.get("", response_model=list[UserResponse])
def list_users(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Return all users (admin only)."""
    return user_repo.list_users(db)


@users_router.post("", response_model=UserResponse, status_code=201)
def create_user(
    payload: CreateUserRequest,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create a new user (admin only). Returns 409 if username is taken."""
    if user_repo.get_by_username(db, payload.username) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Benutzername '{payload.username}' ist bereits vergeben",
        )
    user = user_repo.create_user(db, payload.username, payload.password, role=payload.role)
    db.commit()
    db.refresh(user)
    return user


# ─── Admin: per-user mutations ────────────────────────────────────────────────


@users_router.patch("/{user_id}/role", response_model=UserResponse)
def change_user_role(
    user_id: uuid.UUID,
    payload: ChangeRoleRequest,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Change the role of another user (admin only). Cannot change own role."""
    if user_id == current_admin.id:
        raise HTTPException(status_code=409, detail="Eigene Rolle kann nicht geändert werden")
    user = user_repo.get_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
    user_repo.update_role(db, user, payload.role)
    db.commit()
    db.refresh(user)
    return user


@users_router.patch("/{user_id}/password", status_code=204)
def admin_reset_password(
    user_id: uuid.UUID,
    payload: AdminResetPasswordRequest,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin force-reset another user's password (no current password required)."""
    user = user_repo.get_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
    user_repo.update_password(db, user, payload.new_password)
    db.commit()


@users_router.delete("/{user_id}", status_code=204)
def deactivate_user(
    user_id: uuid.UUID,
    current_admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Deactivate a user account (admin only). Cannot deactivate own account."""
    if user_id == current_admin.id:
        raise HTTPException(
            status_code=409,
            detail="Du kannst dein eigenes Konto nicht deaktivieren",
        )
    user = user_repo.get_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
    user_repo.deactivate(db, user)
    db.commit()
