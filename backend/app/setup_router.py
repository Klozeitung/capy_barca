import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Cookie, HTTPException, Response
from pydantic import BaseModel, Field

from app.database.database import SessionLocal
from app.session.login_router import _COOKIE_MAX_AGE, _COOKIE_NAME, _SECURE
from app.session.session import create_token, validate_token
from app.session.user_registration import create_admin
from app.users import repository as user_repo
from app.users.model import User

setup_router = APIRouter()

# Path to the backup script template served via GET /api/backup/script.
# Defined at module level so tests can monkeypatch it.
_BACKUP_SCRIPT_PATH: Path = (
    Path(__file__).resolve().parents[1] / "recovery" / "backup" / "backup.sh"
)


def _is_configured() -> bool:
    """Return True if at least one user exists in the database."""
    with SessionLocal() as db:
        return db.query(User).count() > 0


def _allow_new_users() -> bool:
    """
    Read ALLOW_NEW_USERS from the environment on every call.

    Reading per-request (rather than at module load time) ensures the
    correct value is always returned after a container restart, without
    requiring a code change or rebuild.
    """
    return os.getenv("ALLOW_NEW_USERS", "false").lower() == "true"


class RegisterRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class SignupRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=8)


@setup_router.get("/api/setup-status")
def setup_status():
    """
    Return the application setup state.

    ``configured``      – True if the initial admin has been created.
    ``allow_new_users`` – True if self-registration is enabled via the
                          ALLOW_NEW_USERS environment variable.
    """
    return {
        "configured": _is_configured(),
        "allow_new_users": _allow_new_users(),
    }


@setup_router.post("/api/register")
def register(payload: RegisterRequest, response: Response):
    """
    Register the initial admin user.

    Blocked once any user exists in the database (returns 403). On success,
    a session token is issued immediately so the client is logged in without
    a separate login request.
    """
    if _is_configured():
        raise HTTPException(status_code=403, detail="Bereits eingerichtet")

    user = create_admin(payload.username, payload.password)

    token = create_token(user.id)
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="strict",
        max_age=_COOKIE_MAX_AGE,
        secure=_SECURE,
    )
    return {"success": True, "username": user.username, "role": user.role}


@setup_router.post("/api/signup")
def signup(payload: SignupRequest, response: Response):
    """
    Self-registration for new users.

    Only available when ``ALLOW_NEW_USERS=true`` is set in the environment.
    The initial admin setup must have been completed first. New users always
    receive the ``member`` role; role elevation is done by an admin via
    ``PATCH /api/users/{id}/role``.
    """
    if not _allow_new_users():
        raise HTTPException(status_code=403, detail="Registrierung ist deaktiviert")
    if not _is_configured():
        raise HTTPException(
            status_code=403,
            detail="Bitte zuerst den Admin-Account einrichten",
        )

    with SessionLocal() as db:
        if user_repo.get_by_username(db, payload.username) is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Benutzername '{payload.username}' ist bereits vergeben",
            )
        user = user_repo.create_user(db, payload.username, payload.password, role="member")
        db.commit()
        db.refresh(user)

    token = create_token(user.id)
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="strict",
        max_age=_COOKIE_MAX_AGE,
        secure=_SECURE,
    )
    return {"success": True, "username": user.username, "role": user.role}


@setup_router.get("/api/backup/script")
def download_backup_script(session: Optional[str] = Cookie(default=None)):
    """
    Return the backup script as a file download.

    The template from recovery/backup/backup.sh is served with REMOTE_HOST
    pre-filled from the TAILSCALE_HOSTNAME environment variable (the server's
    own Tailscale hostname). All other variables (REMOTE_USER, CAPYBARCA_DIR,
    OUTPUT_DIR) remain as placeholders for the user to fill in after download.

    Requires an active session.
    """
    if not session or not validate_token(session):
        raise HTTPException(status_code=401, detail="Nicht angemeldet")

    if not _BACKUP_SCRIPT_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="backup.sh nicht gefunden. Bitte CapyBarca neu installieren.",
        )

    script = _BACKUP_SCRIPT_PATH.read_text(encoding="utf-8")

    tailscale_hostname = os.getenv("TAILSCALE_HOSTNAME", "")
    if tailscale_hostname:
        script = script.replace(
            'REMOTE_HOST="YOUR_TAILSCALE_HOSTNAME_HERE"',
            f'REMOTE_HOST="{tailscale_hostname}"',
        )

    return Response(
        content=script,
        media_type="text/x-sh",
        headers={"Content-Disposition": 'attachment; filename="backup.sh"'},
    )
