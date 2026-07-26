import os
from typing import Optional

from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from pydantic import BaseModel

from app.security.limiter import limiter
from app.session.session import create_token, revoke_token, validate_token

login_router = APIRouter()

_COOKIE_NAME = "session"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7 days
_LOGIN_RATE_LIMIT = os.getenv("LOGIN_RATE_LIMIT", "5/minute")


def cookie_secure() -> bool:
    """
    Return True if the session cookie must carry the ``Secure`` flag.

    DEBUG is read on every call rather than once at import time. An
    import-time read produces a plain bool, and ``from ... import`` copies
    that value into the importing module — the flag can then neither be
    patched centrally in tests nor kept in sync across call sites.

    This mirrors the per-call pattern already used by ``_allow_new_users``
    in ``app.setup_router``.
    """
    return os.getenv("DEBUG", "false").lower() != "true"


def set_session_cookie(response: Response, token: str) -> None:
    """
    Attach the session cookie to ``response``.

    Single definition of the cookie attributes, shared by ``/api/login``,
    ``/api/register`` and ``/api/signup``, so that a change to one attribute
    cannot leave the other endpoints behind.
    """
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="strict",
        max_age=_COOKIE_MAX_AGE,
        secure=cookie_secure(),
    )


class LoginRequest(BaseModel):
    username: str
    password: str


@login_router.post("/api/login")
@limiter.limit(_LOGIN_RATE_LIMIT)
def login(request: Request, payload: LoginRequest, response: Response):
    from app.session.user_login import verifyLogin

    user = verifyLogin(payload.username, payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    set_session_cookie(response, create_token(user.id))
    return {
        "success": True,
        "username": user.username,
        "role": user.role,
        "date_format": user.date_format,
    }


@login_router.get("/api/verify")
def verify(session: Optional[str] = Cookie(default=None)):
    """
    Return 200 with user context if the request carries a valid session
    cookie, 401 otherwise.

    The response includes ``username``, ``role`` and the user's preferred
    ``date_format`` so the frontend can display user information, gate admin
    features, and render dates without a separate profile request.
    """
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user_id = validate_token(session)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    from app.database.database import SessionLocal
    from app.users import repository as user_repo

    with SessionLocal() as db:
        user = user_repo.get_by_id(db, user_id)

    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Not authenticated")

    return {
        "authenticated": True,
        "username": user.username,
        "role": user.role,
        "date_format": user.date_format,
    }


@login_router.post("/api/logout")
def logout(response: Response, session: Optional[str] = Cookie(default=None)):
    """
    Revoke the session token and clear the cookie.

    The clearing cookie repeats the attributes of the issued one so the two
    definitions cannot drift apart; browsers match on name, domain and path.
    """
    if session:
        revoke_token(session)
    response.delete_cookie(
        key=_COOKIE_NAME,
        httponly=True,
        samesite="strict",
        secure=cookie_secure(),
    )
    return {"success": True}
