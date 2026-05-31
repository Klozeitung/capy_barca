import os
from typing import Optional

from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from pydantic import BaseModel

from app.security.limiter import limiter
from app.session.session import create_token, revoke_token, validate_token

login_router = APIRouter()

_COOKIE_NAME = "session"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7 days
_SECURE = os.getenv("DEBUG", "false").lower() != "true"
_LOGIN_RATE_LIMIT = os.getenv("LOGIN_RATE_LIMIT", "5/minute")


class LoginRequest(BaseModel):
    username: str
    password: str


@login_router.post("/api/login")
@limiter.limit(_LOGIN_RATE_LIMIT)
def login(request: Request, payload: LoginRequest, response: Response):
    from app.session.user_login import verifyLogin

    user = verifyLogin(payload.username, payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="Ungültige Anmeldedaten")

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


@login_router.get("/api/verify")
def verify(session: Optional[str] = Cookie(default=None)):
    """
    Return 200 with user context if the request carries a valid session
    cookie, 401 otherwise.

    The response includes ``username`` and ``role`` so the frontend can
    display user information and gate admin features without a separate
    profile request.
    """
    if not session:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")

    user_id = validate_token(session)
    if not user_id:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")

    from app.database.database import SessionLocal
    from app.users import repository as user_repo

    with SessionLocal() as db:
        user = user_repo.get_by_id(db, user_id)

    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Nicht angemeldet")

    return {"authenticated": True, "username": user.username, "role": user.role}


@login_router.post("/api/logout")
def logout(response: Response, session: Optional[str] = Cookie(default=None)):
    """Revoke the session token and clear the cookie."""
    if session:
        revoke_token(session)
    response.delete_cookie(key=_COOKIE_NAME, httponly=True, samesite="strict")
    return {"success": True}
