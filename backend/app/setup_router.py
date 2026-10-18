import hmac
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.database.database import SessionLocal
from app.security.limiter import limiter
from app.session.login_router import set_session_cookie
from app.session.session import create_token, validate_token
from app.session.user_registration import AlreadyConfigured, create_admin
from app.users import repository as user_repo
from app.users.model import User
from app.users.password_rules import NewPassword

setup_router = APIRouter()

# Self-registration hands out an account, so it is throttled like the login
# route. Initial admin registration is one-shot by construction: it refuses
# once any user exists, and concurrent attempts are serialised in
# ``create_admin`` so that only one of them can succeed.
_SIGNUP_RATE_LIMIT = os.getenv("SIGNUP_RATE_LIMIT", "5/minute")

# The initial registration is throttled as well. The setup token makes guessing
# hopeless, but nothing that checks a secret should answer at unlimited speed.
_REGISTER_RATE_LIMIT = os.getenv("REGISTER_RATE_LIMIT", _SIGNUP_RATE_LIMIT)

_INVALID_SETUP_TOKEN = "Invalid setup token"

# Path to the backup script template served via GET /api/backup/script.
# Defined at module level so tests can monkeypatch it.
_BACKUP_SCRIPT_PATH: Path = (
    Path(__file__).resolve().parents[1] / "recovery" / "backup" / "backup.sh"
)


def _is_configured() -> bool:
    """Return True if at least one user exists in the database."""
    with SessionLocal() as db:
        return db.query(User).count() > 0


def _setup_token_matches(supplied: str) -> bool:
    """
    Return True if *supplied* equals the SETUP_TOKEN of this installation.

    Until the first account exists, whoever reaches the setup page becomes the
    administrator. The token closes that window: setup.sh generates it, keeps
    it in .env and prints it to whoever runs the installation, so only someone
    with access to the server can create the first account.

    Read from the environment on every call, like ALLOW_NEW_USERS. Without a
    configured token nothing matches, so an installation that was not set up
    through setup.sh refuses registration instead of falling back to the open
    behaviour. The comparison takes constant time.
    """
    expected = os.getenv("SETUP_TOKEN", "").strip()
    if not expected:
        return False
    return hmac.compare_digest(supplied.strip().encode(), expected.encode())


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
    # The initial administrator used to be the one account that could be
    # created with a one-character password, which is exactly the account
    # worth protecting most.
    password: NewPassword
    # Printed by setup.sh; see _setup_token_matches.
    setup_token: str = Field(min_length=1, max_length=256)


class SignupRequest(BaseModel):
    username: str = Field(min_length=1)
    password: NewPassword


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
@limiter.limit(_REGISTER_RATE_LIMIT)
def register(request: Request, payload: RegisterRequest, response: Response):
    """
    Register the initial admin user.

    Requires the setup token of the installation (returns 403 otherwise).
    Blocked once any user exists in the database (returns 403). The early
    check answers the common case cheaply; ``create_admin`` repeats it under a
    lock, which is what refuses the loser of two simultaneous requests. On
    success, a session token is issued immediately so the client is logged in
    without a separate login request.
    """
    if _is_configured():
        raise HTTPException(status_code=403, detail="Already configured")

    if not _setup_token_matches(payload.setup_token):
        raise HTTPException(status_code=403, detail=_INVALID_SETUP_TOKEN)

    try:
        user = create_admin(payload.username, payload.password)
    except AlreadyConfigured:
        raise HTTPException(status_code=403, detail="Already configured")

    set_session_cookie(response, create_token(user.id))
    return {"success": True, "username": user.username, "role": user.role}


@setup_router.post("/api/signup")
@limiter.limit(_SIGNUP_RATE_LIMIT)
def signup(request: Request, payload: SignupRequest, response: Response):
    """
    Self-registration for new users.

    Only available when ``ALLOW_NEW_USERS=true`` is set in the environment.
    The initial admin setup must have been completed first. New users always
    receive the ``member`` role; role elevation is done by an admin via
    ``PATCH /api/users/{id}/role``.
    """
    if not _allow_new_users():
        raise HTTPException(status_code=403, detail="Registration is disabled")
    if not _is_configured():
        raise HTTPException(
            status_code=403,
            detail="Please set up the admin account first",
        )

    with SessionLocal() as db:
        if user_repo.get_by_username(db, payload.username) is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Username '{payload.username}' is already taken",
            )
        user = user_repo.create_user(db, payload.username, payload.password, role="member")
        db.commit()
        db.refresh(user)

    set_session_cookie(response, create_token(user.id))
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
        raise HTTPException(status_code=401, detail="Not authenticated")

    if not _BACKUP_SCRIPT_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="backup.sh not found. Please reinstall CapyBarca.",
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
