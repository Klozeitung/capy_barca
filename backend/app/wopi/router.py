"""
WOPI server for Collabora Online integration.

Provides CheckFileInfo, GetFile, and PutFile endpoints conforming to the
WOPI protocol, enabling Collabora Online to edit documents stored in Drive
blocks.

Token scheme
------------
An access token is a compact HMAC-signed token that encodes:
  file_uuid  – UUID of the stored file
  block_id   – UUID of the Drive block owning the file
  filename   – original filename (used by Collabora for display and format)
  mime       – MIME type of the file
  username   – display name of the user who issued the token
  exp        – expiry timestamp (Unix, 24 h from issuance)

The token appears both as the WOPI file identifier (URL path segment) and as
the ``access_token`` query parameter in the Collabora editor URL.  The HMAC
signature (SHA-256, keyed with SECRET_KEY) prevents forgery.  WOPI endpoints
do not require a session cookie; the token is the sole access-control layer.

Token format:  base64url(json_payload) + "." + hex(hmac_sha256(payload))
"""
import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.session.session import validate_token

SECRET_KEY: str = os.environ.get("SECRET_KEY", "")
_PORT_FRONTEND: str = os.environ.get("PORT_FRONTEND", "1701")
_TOKEN_TTL: int = 24 * 3600  # seconds

wopi_router = APIRouter(prefix="/api/wopi", tags=["wopi"])


# ─── DB dependency ────────────────────────────────────────────────────────────


def _get_db():
    """Yield a database session scoped to the request."""
    from app.database.database import SessionLocal
    with SessionLocal() as db:
        yield db


# ─── Auth ─────────────────────────────────────────────────────────────────────


def _require_session(session: Optional[str] = Cookie(default=None)) -> str:
    """
    Dependency that enforces a valid session cookie for WOPI token issuance.

    The local ``validate_token`` reference is kept here so that the existing
    test suite can patch ``app.wopi.router.validate_token`` independently of
    the shared deps module.

    Raises
    ------
    HTTPException(401)
        If the cookie is absent or the token is invalid / expired.
    """
    if not session or not validate_token(session):
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    return session


# ─── Token helpers ────────────────────────────────────────────────────────────


def _encode_token(claims: dict) -> str:
    """Encode claims as a self-contained HMAC-signed token string."""
    payload = (
        base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )
    sig = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _decode_token(token: str) -> dict:
    """
    Verify and decode a WOPI token.

    Raises HTTPException 401 on any failure (bad format, wrong signature,
    expired payload).
    """
    parts = token.rsplit(".", 1)
    if len(parts) != 2:
        raise HTTPException(status_code=401, detail="Ungültiger Token")

    payload, sig = parts
    expected = hmac.new(
        SECRET_KEY.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=401, detail="Ungültiger Token")

    try:
        padding = "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload + padding))
    except Exception:
        raise HTTPException(status_code=401, detail="Ungültiger Token")

    if claims.get("exp", 0) < time.time():
        raise HTTPException(status_code=401, detail="Token abgelaufen")

    return claims


# ─── Filesystem helpers ───────────────────────────────────────────────────────


def _static_root() -> Path:
    """Return the current STATIC_ROOT, respecting runtime storage migrations."""
    import app.media.router as media_module

    return media_module.STATIC_ROOT


def _find_file(file_uuid: str, block_id: str) -> Path:
    """
    Locate a drive file on disk by its UUID.

    Globs for ``{file_uuid}*`` inside the block's drive directory so that the
    file extension does not need to be stored in the token.
    """
    drive_dir = _static_root() / "drives" / block_id
    matches = list(drive_dir.glob(f"{file_uuid}*"))
    if not matches:
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    return matches[0]


# ─── Token endpoint ───────────────────────────────────────────────────────────


class TokenRequest(BaseModel):
    file_uuid: str
    block_id: str
    filename: str
    mime: str


class TokenResponse(BaseModel):
    editor_url: str


@wopi_router.post("/token", response_model=TokenResponse)
def create_wopi_token(
    payload: TokenRequest,
    session: str = Depends(_require_session),
    db: Session = Depends(_get_db),
) -> TokenResponse:
    """
    Generate a WOPI access token for a Drive file and return the full
    Collabora editor URL to be used as an iframe ``src``.

    The ``WOPISrc`` embedded in the URL points to the nginx frontend service
    (``https://frontend:{PORT_FRONTEND}``) which Collabora can reach inside
    the Docker network via the ``/api/wopi/`` proxy location.
    """
    from urllib.parse import quote

    from app.users import repository as user_repo

    # Resolve the display name from the session so it can be embedded in the
    # WOPI token for Collabora's UI (does not affect access control).
    # Guard against non-UUID values: test suites may patch validate_token to
    # return a truthy sentinel (e.g. True) rather than an actual UUID.
    user_id = validate_token(session)
    user = user_repo.get_by_id(db, user_id) if isinstance(user_id, uuid.UUID) else None
    username = user.username if user else "CapyBarca"

    now = int(time.time())
    claims = {
        "file_uuid": payload.file_uuid,
        "block_id": payload.block_id,
        "filename": payload.filename,
        "mime": payload.mime,
        "username": username,
        "iat": now,
        "exp": now + _TOKEN_TTL,
    }
    token = _encode_token(claims)

    wopi_src = f"https://frontend:{_PORT_FRONTEND}/api/wopi/files/{token}"
    editor_url = (
        f"/collabora/browser/dist/cool.html"
        f"?WOPISrc={quote(wopi_src, safe='')}"
        f"&access_token={token}"
        f"&access_token_ttl=0"
        f"&lang=de"
    )
    return TokenResponse(editor_url=editor_url)


# ─── WOPI: CheckFileInfo ──────────────────────────────────────────────────────


@wopi_router.get("/files/{token}")
def check_file_info(token: str) -> JSONResponse:
    """
    Return file metadata conforming to the WOPI CheckFileInfo specification.

    Collabora calls this endpoint first to learn the file name, size,
    modification time, and user capabilities before loading the document.
    """
    claims = _decode_token(token)
    file_path = _find_file(claims["file_uuid"], claims["block_id"])
    stat = file_path.stat()
    last_modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    username = claims.get("username", "CapyBarca")

    return JSONResponse(
        {
            "BaseFileName": claims["filename"],
            "Size": stat.st_size,
            "LastModifiedTime": last_modified,
            "OwnerId": username,
            "UserId": username,
            "UserFriendlyName": username,
            "UserCanWrite": True,
            "SupportsUpdate": True,
            "SupportsLocks": False,
            "DisablePrint": False,
        }
    )


# ─── WOPI: GetFile ────────────────────────────────────────────────────────────


@wopi_router.get("/files/{token}/contents")
def get_file(token: str) -> Response:
    """
    Return the raw file bytes (WOPI GetFile action).

    Collabora fetches the document binary before rendering it.
    """
    claims = _decode_token(token)
    file_path = _find_file(claims["file_uuid"], claims["block_id"])
    return Response(
        content=file_path.read_bytes(),
        media_type=claims.get("mime", "application/octet-stream"),
    )


# ─── WOPI: PutFile ────────────────────────────────────────────────────────────


@wopi_router.post("/files/{token}/contents")
async def put_file(token: str, request: Request) -> JSONResponse:
    """
    Persist updated file bytes from Collabora (WOPI PutFile action).

    Called by Collabora whenever the user saves the document (auto-save or
    explicit Ctrl+S).  The entire file body is written atomically so that a
    failed write never leaves a truncated file.
    """
    claims = _decode_token(token)
    file_path = _find_file(claims["file_uuid"], claims["block_id"])
    body = await request.body()

    # Atomic write: write to a sibling temp file then rename.
    tmp_path = file_path.with_suffix(".tmp")
    try:
        tmp_path.write_bytes(body)
        tmp_path.replace(file_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)

    return JSONResponse({"status": "ok"})
