"""
User management router.

Password fields use the shared types from ``app.users.password_rules`` rather
than plain strings, so that the minimum length and bcrypt's 72-byte ceiling are
stated once for the whole application instead of per endpoint.
"""
import os
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.security.limiter import limiter
from app.session.deps import get_current_user, get_db, require_admin, require_session
from app.users import repository as user_repo
from app.users.model import User
from app.users.password_rules import ExistingPassword, NewPassword

users_router = APIRouter(prefix="/api/users", tags=["users"])

# Changing a password checks the current one, which makes this endpoint a
# password oracle for anyone who has taken over a session. Throttled for the
# same reason /api/login is.
_PASSWORD_CHANGE_RATE_LIMIT = os.getenv("PASSWORD_CHANGE_RATE_LIMIT", "5/minute")

# Canonical display date-format tokens a user may choose as their global
# preference. These govern frontend rendering only; dates are always stored
# and exchanged as ISO 8601. The per-property "global" sentinel is a database
# property concept and is intentionally NOT a valid user-level value here.
DateFormatToken = Literal["DD.MM.YYYY", "MM.DD.YYYY", "YYYY-MM-DD", "YYYY-DD-MM"]


# ─── Schemas ──────────────────────────────────────────────────────────────────


class UserNameResponse(BaseModel):
    id: uuid.UUID
    username: str


class UserResponse(BaseModel):
    id: uuid.UUID
    username: str
    role: str
    is_active: bool
    date_format: str

    model_config = {"from_attributes": True}


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1)
    password: NewPassword
    role: str = Field(default="member", pattern="^(admin|member)$")


class ChangeUsernameRequest(BaseModel):
    username: str = Field(min_length=1)


class ChangeDateFormatRequest(BaseModel):
    date_format: DateFormatToken


class ChangePasswordRequest(BaseModel):
    # The current password carries only the upper bound: an account created
    # before the minimum existed must still be able to change its password.
    current_password: ExistingPassword
    new_password: NewPassword


class AdminResetPasswordRequest(BaseModel):
    new_password: NewPassword


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
                detail=f"Username '{payload.username}' is already taken",
            )
        user_repo.update_username(db, current_user, payload.username)
        db.commit()
        db.refresh(current_user)
    return current_user


@users_router.patch("/me/password", status_code=204)
@limiter.limit(_PASSWORD_CHANGE_RATE_LIMIT)
def change_own_password(
    request: Request,
    payload: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Change own password. Requires the correct current password."""
    if not user_repo.verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(status_code=401, detail="The current password is incorrect")
    user_repo.update_password(db, current_user, payload.new_password)
    db.commit()


@users_router.patch("/me/date-format", response_model=UserResponse)
def change_own_date_format(
    payload: ChangeDateFormatRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Set the preferred display date format for the current user.

    Invalid tokens are rejected by request validation (422). The value affects
    frontend rendering only; stored dates remain ISO 8601.
    """
    user_repo.update_date_format(db, current_user, payload.date_format)
    db.commit()
    db.refresh(current_user)
    return current_user


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
            detail=f"Username '{payload.username}' is already taken",
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
        raise HTTPException(status_code=409, detail="You cannot change your own role")
    user = user_repo.get_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
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
        raise HTTPException(status_code=404, detail="User not found")
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
            detail="You cannot deactivate your own account",
        )
    user = user_repo.get_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user_repo.deactivate(db, user)
    db.commit()
