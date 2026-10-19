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
  user_id    – UUID of the user the token was issued to
  username   – display name of that user
  jti        – UUID of the token's row in the registry (see below)
  iat        – issuance timestamp (Unix)
  exp        – expiry timestamp (Unix, 24 h from issuance)

The token appears both as the WOPI file identifier (URL path segment) and as
the ``access_token`` query parameter in the Collabora editor URL.

Token format:  base64url(json_payload) + "." + hex(hmac_sha256(payload))

Access control
--------------
Collabora reaches the file endpoints from inside the Compose network and
carries no session cookie, so the token is the only thing it presents. The
token alone is not what grants access, however:

* Issuance requires a session and the same block permission check every other
  block-addressing endpoint performs, so a token can only be minted for a
  block the caller may already reach.
* Every file endpoint resolves ``user_id`` from the claims and re-runs the
  permission check against current state. A token therefore stops working the
  moment the account is deactivated or loses access, rather than staying valid
  for its full lifetime.
* Opening a document needs read access; saving it (PutFile) needs write
  access. CheckFileInfo reports the current level as ``UserCanWrite``, so
  Collabora opens a document read-only for a reader.
* The server remembers every token it issues (``app.wopi.registry``). A file
  endpoint accepts a token only while its ``jti`` names a registered row for
  the same account, block and file. Claims signed by someone else, even with
  the right key, name no such row. Logging out deletes the rows issued in
  that session, and changing the password deletes all rows of the account,
  which ends the editor sessions they belong to.

An empty or placeholder ``SECRET_KEY`` is refused at startup (see
``require_signing_secret``), so tokens are never signed with a known key. The
signing key is derived rather than used directly, so a WOPI token cannot be
replayed against anything else that happens to be keyed with the same secret.

Editor language
---------------
The token request names the interface language Collabora starts in
(``lang``). Only the languages of the frontend are accepted; the default is
German.
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
from typing import Literal, Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.security.limiter import limiter
from app.permissions.model import READ, WRITE
from app.session.deps import get_current_user, get_db, require_block_access
from app.users.model import User
from app.wopi import registry

SECRET_KEY: str = os.environ.get("SECRET_KEY", "")

# Values that look like a secret but are not one: the placeholder .env.example
# ships with. A deployment that kept it signs with a publicly known key.
_PLACEHOLDER_SECRETS: frozenset[str] = frozenset({"changeme"})

_PORT_FRONTEND: str = os.environ.get("PORT_FRONTEND", "1701")
_TOKEN_TTL: int = 24 * 3600  # seconds

# Issuance is a cheap call that mints a bearer credential, so it is throttled
# like the other credential-producing routes. The file endpoints are not:
# Collabora calls them repeatedly during an editing session.
_WOPI_TOKEN_RATE_LIMIT = os.getenv("WOPI_TOKEN_RATE_LIMIT", "30/minute")

_INVALID_TOKEN = "Invalid token"

wopi_router = APIRouter(prefix="/api/wopi", tags=["wopi"])


# ─── Token helpers ────────────────────────────────────────────────────────────


def require_signing_secret() -> None:
    """
    Refuse to operate without a usable SECRET_KEY.

    An empty key still produces valid-looking HMAC signatures, so anyone who
    knows the token format could sign claims naming any account. Called from
    the application's startup so a misconfigured installation fails loudly
    instead of serving tokens signed with nothing, and from every signing
    operation as a second line, since SECRET_KEY can be substituted at runtime.

    Raises
    ------
    RuntimeError
        If SECRET_KEY is empty, whitespace only, or the documented placeholder.
    """
    key = SECRET_KEY.strip()
    if not key or key in _PLACEHOLDER_SECRETS:
        raise RuntimeError(
            "SECRET_KEY is not set or still holds a placeholder. setup.sh "
            "generates one in .env; set it there and restart the backend."
        )


def _signing_key() -> bytes:
    """
    Return the key used to sign WOPI tokens.

    Derived from SECRET_KEY rather than being SECRET_KEY, so that a token
    minted here cannot be presented anywhere else that signs with the same
    secret, and so a future rotation can replace this one key on its own.
    Recomputed per call because SECRET_KEY is a module attribute the test
    suite substitutes.
    """
    require_signing_secret()
    return hmac.new(SECRET_KEY.encode(), b"capybarca-wopi-token-v1", hashlib.sha256).digest()


def _encode_token(claims: dict) -> str:
    """Encode claims as a self-contained HMAC-signed token string."""
    payload = (
        base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )
    sig = hmac.new(_signing_key(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _decode_token(token: str) -> dict:
    """
    Verify and decode a WOPI token.

    Raises HTTPException 401 on any failure: bad format, wrong signature,
    expired payload, or claims that do not carry the identifiers this router
    needs. The identifier check matters because those values end up in a
    filesystem path, and a signature only proves the claims were not altered
    in transit, not that they are sane.
    """
    parts = token.rsplit(".", 1)
    if len(parts) != 2:
        raise HTTPException(status_code=401, detail=_INVALID_TOKEN)

    payload, sig = parts
    expected = hmac.new(_signing_key(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(status_code=401, detail=_INVALID_TOKEN)

    try:
        padding = "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload + padding))
    except Exception:
        raise HTTPException(status_code=401, detail=_INVALID_TOKEN)

    if not isinstance(claims, dict):
        raise HTTPException(status_code=401, detail=_INVALID_TOKEN)

    if claims.get("exp", 0) < time.time():
        raise HTTPException(status_code=401, detail="Token expired")

    for field in ("file_uuid", "block_id", "user_id", "jti"):
        try:
            uuid.UUID(str(claims.get(field)))
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(status_code=401, detail=_INVALID_TOKEN)

    return claims


# ─── Authorization ────────────────────────────────────────────────────────────


def _authorize_claims(db: Session, claims: dict, level: str = READ) -> User:
    """
    Confirm the token is registered, resolve the account it was issued to and
    confirm that account still has *level* access to the block.

    This is what keeps the token from being the sole control. Without it a
    token stays usable for its entire lifetime no matter what happens to the
    account or to the block's permissions in the meantime.

    Raises
    ------
    HTTPException(401)
        The token is not registered (never issued, withdrawn or expired), or
        the account no longer exists or has been deactivated.
    HTTPException(403)
        The account exists but may no longer reach the block, or lacks *level*.
    """
    from app.users import repository as user_repo

    if not registry.is_registered(
        db,
        jti=uuid.UUID(str(claims["jti"])),
        user_id=uuid.UUID(str(claims["user_id"])),
        block_id=uuid.UUID(str(claims["block_id"])),
        file_uuid=uuid.UUID(str(claims["file_uuid"])),
    ):
        raise HTTPException(status_code=401, detail=_INVALID_TOKEN)

    user = user_repo.get_by_id(db, uuid.UUID(str(claims["user_id"])))
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail=_INVALID_TOKEN)

    require_block_access(db, uuid.UUID(str(claims["block_id"])), user, level=level)
    return user


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

    The resolved directory is confirmed to sit inside the upload tree. Both
    identifiers are validated as UUIDs during decoding, so nothing can escape
    today; the check stays because the consequence of a future change here is
    reading or overwriting a file anywhere the process can reach.
    """
    root = _static_root().resolve()
    drive_dir = root / "drives" / block_id
    if root not in drive_dir.resolve().parents:
        raise HTTPException(status_code=400, detail="Invalid storage path")

    matches = [p for p in drive_dir.glob(f"{file_uuid}*") if not p.name.startswith(".")]
    if not matches:
        raise HTTPException(status_code=404, detail="File not found")
    return matches[0]


# ─── Token endpoint ───────────────────────────────────────────────────────────

# Interface languages Collabora may be started in: those of the frontend.
EditorLanguage = Literal["de", "en"]


class TokenRequest(BaseModel):
    # Typed as UUID rather than str: both values are written into a filesystem
    # path once the token comes back, and the caller signs nothing here - the
    # server does. A relative segment would otherwise be carried inside a
    # perfectly valid signature.
    file_uuid: uuid.UUID
    block_id: uuid.UUID
    filename: str
    mime: str
    lang: EditorLanguage = "de"


class TokenResponse(BaseModel):
    editor_url: str


@wopi_router.post("/token", response_model=TokenResponse)
@limiter.limit(_WOPI_TOKEN_RATE_LIMIT)
def create_wopi_token(
    request: Request,
    payload: TokenRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    session: Optional[str] = Cookie(default=None),
) -> TokenResponse:
    """
    Generate a WOPI access token for a Drive file and return the full
    Collabora editor URL to be used as an iframe ``src``.

    The ``WOPISrc`` embedded in the URL points to the nginx frontend service
    (``https://frontend:{PORT_FRONTEND}``) which Collabora can reach inside
    the Docker network via the ``/api/wopi/`` proxy location.

    A session used to be the only requirement, which meant any account could
    mint an editing token for any drive file by naming its ids. The block
    permission check closes that. Read access is enough to open a document;
    whether it may be saved is decided per request (see PutFile).

    The token is registered before it is returned, tied to the caller's
    session (see the module docstring); expired tokens are removed in the
    same transaction. ``lang`` selects the
    editor's interface language. Answers 404 for a block that does not exist.
    """
    from urllib.parse import quote

    from app.blocks import repository as block_repo
    from app.session.session import record_key

    require_block_access(db, payload.block_id, user, level=READ)
    if block_repo.get_block(db, payload.block_id) is None:
        # The permission layer admits a block that does not exist; there is
        # no file to edit in it, and the registry row needs the block.
        raise HTTPException(status_code=404, detail="Block not found")

    now = int(time.time())
    jti = uuid.uuid4()
    claims = {
        "file_uuid": str(payload.file_uuid),
        "block_id": str(payload.block_id),
        "filename": payload.filename,
        "mime": payload.mime,
        "user_id": str(user.id),
        "username": user.username,
        "jti": str(jti),
        "iat": now,
        "exp": now + _TOKEN_TTL,
    }
    token = _encode_token(claims)
    registry.register(
        db,
        jti=jti,
        user_id=user.id,
        block_id=payload.block_id,
        file_uuid=payload.file_uuid,
        issued_at=datetime.fromtimestamp(now, tz=timezone.utc),
        expires_at=datetime.fromtimestamp(now + _TOKEN_TTL, tz=timezone.utc),
        session_key=record_key(session, db) if session else None,
    )
    db.commit()

    wopi_src = f"https://frontend:{_PORT_FRONTEND}/api/wopi/files/{token}"
    editor_url = (
        f"/collabora/browser/dist/cool.html"
        f"?WOPISrc={quote(wopi_src, safe='')}"
        f"&access_token={token}"
        f"&access_token_ttl=0"
        f"&lang={payload.lang}"
    )
    return TokenResponse(editor_url=editor_url)


# ─── WOPI: CheckFileInfo ──────────────────────────────────────────────────────


@wopi_router.get("/files/{token}")
def check_file_info(token: str, db: Session = Depends(get_db)) -> JSONResponse:
    """
    Return file metadata conforming to the WOPI CheckFileInfo specification.

    Collabora calls this endpoint first to learn the file name, size,
    modification time, and user capabilities before loading the document.

    ``UserCanWrite`` reports what the account may actually do right now:
    true with write access to the block, false for a reader.
    """
    from app.permissions import repository as perm_repo

    claims = _decode_token(token)
    user = _authorize_claims(db, claims)

    # Asked from the same source that governs every other write to the block.
    user_can_write = perm_repo.can_user_access(
        db, uuid.UUID(str(claims["block_id"])), user, level=WRITE
    )

    file_path = _find_file(claims["file_uuid"], claims["block_id"])
    stat = file_path.stat()
    last_modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    username = claims.get("username", user.username)

    return JSONResponse(
        {
            "BaseFileName": claims["filename"],
            "Size": stat.st_size,
            "LastModifiedTime": last_modified,
            "OwnerId": username,
            "UserId": username,
            "UserFriendlyName": username,
            "UserCanWrite": user_can_write,
            "SupportsUpdate": True,
            "SupportsLocks": False,
            "DisablePrint": False,
        }
    )


# ─── WOPI: GetFile ────────────────────────────────────────────────────────────


@wopi_router.get("/files/{token}/contents")
def get_file(token: str, db: Session = Depends(get_db)) -> FileResponse:
    """
    Return the raw file bytes (WOPI GetFile action).

    Collabora fetches the document binary before rendering it. Streamed from
    disk rather than read into memory first, so the size of a document does
    not translate into resident memory on every open.
    """
    claims = _decode_token(token)
    _authorize_claims(db, claims)

    file_path = _find_file(claims["file_uuid"], claims["block_id"])
    return FileResponse(
        path=file_path,
        media_type=claims.get("mime", "application/octet-stream"),
    )


# ─── WOPI: PutFile ────────────────────────────────────────────────────────────


@wopi_router.post("/files/{token}/contents")
async def put_file(token: str, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """
    Persist updated file bytes from Collabora (WOPI PutFile action).

    Called by Collabora whenever the user saves the document (auto-save or
    explicit Ctrl+S). The body is streamed to a temporary file and moved into
    place, so a failed or oversized write never leaves a truncated document.

    The ceiling comes from ``app.media.upload``, the same one the upload and
    cover endpoints enforce. Reading the whole body first would make the memory
    cost of a save the caller's choice.

    Collabora sends a raw request body rather than a multipart part, so this
    calls ``write_stream`` directly instead of the multipart helper.

    The handler stays ``async`` because the body is still arriving while it is
    written. The authorization check and the file lookup are blocking, so
    they run in a worker thread and the event loop keeps serving other
    requests meanwhile.

    Saving needs write access; a reader is refused with 403 even if a client
    ignores ``UserCanWrite``.
    """
    from app.media import upload as upload_helper

    claims = _decode_token(token)
    await run_in_threadpool(_authorize_claims, db, claims, WRITE)

    file_path = await run_in_threadpool(_find_file, claims["file_uuid"], claims["block_id"])

    # The temporary name starts with a dot so that _find_file's glob cannot
    # pick it up while the write is in flight, and so a leftover from a
    # crashed save does not shadow the document afterwards.
    tmp_path = file_path.parent / f".{file_path.name}.tmp"

    try:
        await upload_helper.write_stream(request.stream(), tmp_path)
        tmp_path.replace(file_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    return JSONResponse({"status": "ok"})
