"""
Media router.

Handles file uploads, deletions, and bookmark metadata fetching for media
(image, video, audio, pdf), file, and drive block types.

Storage layout
--------------
  static/uploads/media/{image|video|audio|pdf}/{file_uuid}{ext}
  static/uploads/files/{file_uuid}{ext}
  static/uploads/drives/{block_id}/{file_uuid}{ext}

All uploaded files are linked to a block via the UUID stored in the block's
``content`` JSON field. The router does not mutate blocks directly; that
responsibility stays with the caller (frontend updates via the blocks API).

Authorization
-------------
Every endpoint that names a block enforces ``require_block_access`` on top of
the session check, so an authenticated account cannot reach a block it has no
permission for by guessing its id.

Outbound requests
-----------------
``fetch_bookmark`` is the only endpoint that makes the server open a connection
to an address the caller chose. Every target, including each redirect hop, is
resolved and checked against an allowlist of publicly routable addresses before
a socket is opened, the response body is capped, and the preview asset URLs
handed back to the browser are restricted to https.

Note on the shared namespaces: drive files live under a per-block directory,
so the block check fully governs them. Media and file uploads land in one flat
directory per category, and the server keeps no mapping from ``file_uuid`` back
to its owning block. For those two categories the block check therefore governs
where a file may be written, but a caller who already knows a ``file_uuid`` can
still address it through any block id. Closing that needs a persisted
file-to-block mapping and is out of scope here.
"""
import asyncio
import ipaddress
import mimetypes
import os
import re
import shutil
import socket
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.security.limiter import limiter
from app.session.deps import get_current_user, get_db, require_block_access
from app.users.model import User

# Root for all uploaded files. Always the default static/uploads directory.
STATIC_ROOT: Path = Path("static/uploads")

MEDIA_CATEGORIES: frozenset[str] = frozenset({"image", "video", "audio", "pdf"})
VALID_CATEGORIES: frozenset[str] = MEDIA_CATEGORIES | frozenset({"file", "drive"})

# ── Upload limits and permitted types ─────────────────────────────────────────
# The ceiling is enforced here as well as in nginx. nginx stops an oversized
# body at the edge; this stops one that arrives by any other route and produces
# a JSON answer the frontend can present, instead of nginx's HTML error page.
_MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "100")) * 1024 * 1024
_UPLOAD_CHUNK_BYTES = 1024 * 1024
_UPLOAD_TOO_LARGE = "The file exceeds the upload size limit"
_UPLOAD_TYPE_REFUSED = "This file type is not allowed for this block"

# Extensions the four media categories accept. The list is deliberately short
# and excludes SVG: an SVG is a document that can carry script, and an image
# block renders its source inline.
#
# The 'file' and 'drive' categories take arbitrary attachments and are absent
# here on purpose. What makes them safe is the delivery side, where anything
# outside INLINE_MEDIA_TYPES in app/main.py is handed out as a download with
# a neutral content type.
_CATEGORY_EXTENSIONS: dict[str, frozenset[str]] = {
    "image": frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".bmp"}),
    "video": frozenset({".mp4", ".webm", ".ogv", ".mov", ".m4v"}),
    "audio": frozenset({".mp3", ".wav", ".ogg", ".oga", ".m4a", ".flac", ".aac"}),
    "pdf": frozenset({".pdf"}),
}

# ── Bookmark fetching ─────────────────────────────────────────────────────────
# One message for every refusal reason. Distinguishing "blocked address" from
# "does not resolve" would turn the endpoint into a probe for which internal
# names exist.
_BOOKMARK_REFUSED = "This address cannot be loaded"
_BOOKMARK_SCHEMES: frozenset[str] = frozenset({"http", "https"})
_BOOKMARK_TIMEOUT = 10.0
_BOOKMARK_MAX_BYTES = 512 * 1024
_BOOKMARK_MAX_REDIRECTS = 3
_BOOKMARK_USER_AGENT = "Mozilla/5.0 (compatible; CapyBarca/1.0)"
_BOOKMARK_RATE_LIMIT = os.getenv("BOOKMARK_RATE_LIMIT", "10/minute")

media_router = APIRouter(prefix="/api/media", tags=["media"])


# ─── Internal helpers ─────────────────────────────────────────────────────────


def _within_root(path: Path) -> Path:
    """
    Return *path* unchanged if it stays inside ``STATIC_ROOT``, else refuse.

    Every identifier that builds a storage path is typed as ``uuid.UUID``, so
    no request can currently escape. This is kept as a second line of defence
    because the cost of a future mistake here is a write or a delete anywhere
    the process can reach, and the check is a single ``resolve`` call.
    """
    root = STATIC_ROOT.resolve()
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise HTTPException(status_code=400, detail="Invalid storage path")
    return path


def _storage_dir(category: str, block_id: str) -> Path:
    """Return the storage directory for a given category and block_id."""
    if category in MEDIA_CATEGORIES:
        return _within_root(STATIC_ROOT / "media" / category)
    if category == "file":
        return _within_root(STATIC_ROOT / "files")
    # category == "drive"
    return _within_root(STATIC_ROOT / "drives" / block_id)


def _public_url(category: str, block_id: str, stored_name: str) -> str:
    """Build the public-facing URL for a stored file."""
    if category in MEDIA_CATEGORIES:
        return f"/static/uploads/media/{category}/{stored_name}"
    if category == "file":
        return f"/static/uploads/files/{stored_name}"
    return f"/static/uploads/drives/{block_id}/{stored_name}"


def _extract_og(html: str, property_name: str) -> Optional[str]:
    """Extract the ``content`` attribute of an Open Graph ``<meta>`` tag."""
    escaped = re.escape(property_name)
    m = re.search(
        rf'<meta[^>]+property=["\']?{escaped}["\']?[^>]+content=["\']([^"\']*)["\']',
        html,
        re.IGNORECASE,
    )
    if m:
        return m.group(1)
    m2 = re.search(
        rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+property=["\']?{escaped}["\']?',
        html,
        re.IGNORECASE,
    )
    return m2.group(1) if m2 else None


def _extract_title_tag(html: str) -> Optional[str]:
    m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
    return m.group(1).strip() if m else None


def _extract_meta_name(html: str, name: str) -> Optional[str]:
    escaped = re.escape(name)
    m = re.search(
        rf'<meta[^>]+name=["\']?{escaped}["\']?[^>]+content=["\']([^"\']*)["\']',
        html,
        re.IGNORECASE,
    )
    if m:
        return m.group(1)
    m2 = re.search(
        rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+name=["\']?{escaped}["\']?',
        html,
        re.IGNORECASE,
    )
    return m2.group(1) if m2 else None


# ─── Outbound target validation ───────────────────────────────────────────────


def _ip_is_publicly_routable(raw: str) -> bool:
    """
    Return True only for addresses that belong to the public internet.

    Written as an allowlist rather than a list of forbidden ranges: a blocklist
    has to be extended every time a new special-purpose range is assigned,
    whereas ``is_global`` denies anything that is not unambiguously public.
    That already covers loopback, RFC1918, link-local including the cloud
    metadata address, unique-local, and the carrier-grade NAT range Tailscale
    hands out. Multicast is excluded separately because it reports as global.
    """
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return False

    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        # ::ffff:127.0.0.1 must be judged as the IPv4 address it carries.
        address = mapped

    return address.is_global and not address.is_multicast


async def _resolve_host(host: str, port: int) -> list[str]:
    """
    Return every address a connection to *host* could end up using.

    Separate function so tests can substitute it, and so the blocking resolver
    call runs off the event loop.
    """
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


async def _assert_target_allowed(url: str) -> None:
    """
    Refuse *url* unless it names a public http(s) endpoint.

    Every address the hostname resolves to has to qualify, not merely the
    first: a name with both a public and a private record would otherwise be
    a reliable way in.

    Residual: the resolver runs again inside httpx when the connection is
    opened, so a record that changes between the two lookups is not caught
    here. Closing that means pinning the connection to the address that was
    checked, which costs correct TLS naming for every ordinary bookmark.
    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        raise HTTPException(status_code=400, detail=_BOOKMARK_REFUSED)

    if parsed.scheme not in _BOOKMARK_SCHEMES or not hostname:
        raise HTTPException(status_code=400, detail=_BOOKMARK_REFUSED)

    # A literal address is judged as it stands. Sending it through the resolver
    # first would make the verdict depend on a lookup that has nothing left to
    # decide, and any influence over that lookup would become influence over
    # the verdict.
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        if not _ip_is_publicly_routable(hostname):
            raise HTTPException(status_code=400, detail=_BOOKMARK_REFUSED)
        return

    try:
        addresses = await _resolve_host(hostname, port)
    except Exception:
        raise HTTPException(status_code=400, detail=_BOOKMARK_REFUSED)

    if not addresses or not all(_ip_is_publicly_routable(a) for a in addresses):
        raise HTTPException(status_code=400, detail=_BOOKMARK_REFUSED)


async def _fetch_preview_html(url: str) -> tuple[str, str]:
    """
    Fetch *url* and return ``(final_url, html)``.

    Redirects are followed by hand instead of by httpx so that every hop is
    validated. Automatic following would check the address the user supplied
    and then quietly go wherever that address points.
    """
    current = url
    headers = {"User-Agent": _BOOKMARK_USER_AGENT}

    async with httpx.AsyncClient(
        follow_redirects=False, timeout=_BOOKMARK_TIMEOUT
    ) as client:
        for _ in range(_BOOKMARK_MAX_REDIRECTS + 1):
            await _assert_target_allowed(current)

            async with client.stream("GET", current, headers=headers) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise HTTPException(
                            status_code=400, detail=_BOOKMARK_REFUSED
                        )
                    current = urljoin(current, location)
                    continue

                # Read at most the cap: a preview needs the document head, and
                # an unbounded read would let any page decide how much memory
                # the server spends.
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= _BOOKMARK_MAX_BYTES:
                        break

                body = b"".join(chunks)[:_BOOKMARK_MAX_BYTES]
                return current, body.decode(
                    response.encoding or "utf-8", errors="replace"
                )

    raise HTTPException(status_code=400, detail=_BOOKMARK_REFUSED)


def _safe_preview_asset(candidate: Optional[str], base_url: str) -> Optional[str]:
    """
    Return an image URL the browser may load, or None.

    These values come out of a foreign document and end up in an ``img`` tag,
    which makes them a request the user's browser performs on the page's
    behalf. Restricting them to https on a public host keeps that request from
    reaching anything on the user's own network, and drops mixed content the
    browser would refuse anyway.

    Relative references are resolved against the page they were found on,
    which also makes previews work for the many sites that use them.
    """
    if not candidate:
        return None

    absolute = urljoin(base_url, candidate.strip())
    parsed = urlparse(absolute)
    if parsed.scheme != "https" or not parsed.hostname:
        return None

    # A literal address can be judged here; a name is left to the browser,
    # since resolving it server-side would say nothing about what the client
    # will resolve it to.
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return absolute
    return absolute if _ip_is_publicly_routable(parsed.hostname) else None


# ─── Schemas ──────────────────────────────────────────────────────────────────


class UploadResponse(BaseModel):
    file_uuid: str
    url: str
    filename: str
    size: int
    mime: str


class DeleteResponse(BaseModel):
    deleted: str


class BookmarkRequest(BaseModel):
    url: str


class BookmarkResponse(BaseModel):
    url: str
    title: Optional[str] = None
    description: Optional[str] = None
    image: Optional[str] = None
    favicon: Optional[str] = None


class CapacityResponse(BaseModel):
    total_bytes: int
    used_bytes: int
    free_bytes: int


# ─── Endpoints ────────────────────────────────────────────────────────────────


@media_router.get("/capacity", response_model=CapacityResponse)
def get_capacity(
    _user: User = Depends(get_current_user),
) -> CapacityResponse:
    """
    Return disk capacity information for the upload storage directory.

    Reports the total, used, and free bytes of the filesystem partition that
    holds ``STATIC_ROOT``. The directory is created eagerly if it does not
    exist yet so that a fresh installation does not cause a 500.

    No block is addressed, so a valid session is the only requirement.
    """
    STATIC_ROOT.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(STATIC_ROOT)
    return CapacityResponse(
        total_bytes=usage.total,
        used_bytes=usage.used,
        free_bytes=usage.free,
    )


@media_router.post("/upload/{category}/{block_id}", response_model=UploadResponse)
async def upload_file(
    category: str,
    block_id: uuid.UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> UploadResponse:
    """
    Upload a file and store it under the appropriate static sub-path.

    Returns the generated ``file_uuid``, public URL, original filename,
    byte size, and MIME type. The caller is responsible for persisting these
    values in the block's ``content`` field via the blocks API.

    Refuses a type the category does not accept with 415, and an upload past
    the size ceiling with 413. A partial file from a refused upload is removed
    before the error is raised.
    """
    if category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Unknown category: {category!r}")

    require_block_access(db, block_id, user)

    block_id_str = str(block_id)
    file_uuid = str(uuid.uuid4())
    original_name = file.filename or "upload"
    ext = Path(original_name).suffix.lower()
    if not ext and file.content_type:
        ext = mimetypes.guess_extension(file.content_type) or ""

    permitted = _CATEGORY_EXTENSIONS.get(category)
    if permitted is not None and ext not in permitted:
        raise HTTPException(status_code=415, detail=_UPLOAD_TYPE_REFUSED)

    stored_name = f"{file_uuid}{ext}"
    dest_dir = _storage_dir(category, block_id_str)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / stored_name

    # Written in chunks rather than read whole: a single read pulls the entire
    # upload into memory before anything is checked, so the size limit would
    # arrive after the damage. Streaming also lets the ceiling stop the write
    # at the moment it is crossed.
    size = 0
    too_large = False
    with dest_path.open("wb") as sink:
        while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
            size += len(chunk)
            if size > _MAX_UPLOAD_BYTES:
                too_large = True
                break
            sink.write(chunk)

    if too_large:
        dest_path.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail=_UPLOAD_TOO_LARGE)

    return UploadResponse(
        file_uuid=file_uuid,
        url=_public_url(category, block_id_str, stored_name),
        filename=original_name,
        size=size,
        mime=file.content_type or "application/octet-stream",
    )


@media_router.delete("/{category}/{block_id}/{file_uuid}", response_model=DeleteResponse)
async def delete_file(
    category: str,
    block_id: uuid.UUID,
    file_uuid: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DeleteResponse:
    """
    Delete a previously uploaded file identified by its UUID.

    Authorization is checked before existence so that a caller without access
    cannot use the status code to learn whether a file is there.
    """
    if category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Unknown category: {category!r}")

    require_block_access(db, block_id, user)

    block_id_str = str(block_id)
    file_uuid_str = str(file_uuid)
    dest_dir = _storage_dir(category, block_id_str)
    if not dest_dir.exists():
        raise HTTPException(status_code=404, detail="File not found")

    matches = list(dest_dir.glob(f"{file_uuid_str}*"))
    if not matches:
        raise HTTPException(status_code=404, detail="File not found")

    for path in matches:
        path.unlink(missing_ok=True)

    return DeleteResponse(deleted=file_uuid_str)


@media_router.post("/bookmark", response_model=BookmarkResponse)
@limiter.limit(_BOOKMARK_RATE_LIMIT)
async def fetch_bookmark(
    request: Request,
    payload: BookmarkRequest,
    _user: User = Depends(get_current_user),
) -> BookmarkResponse:
    """
    Fetch Open Graph / meta data for a URL to build a bookmark preview.

    This is the one place where the caller decides which address the server
    connects to, so the target is validated before every hop and the endpoint
    is rate limited: without a limit it doubles as a fast scanner for whatever
    the server can reach.

    A refused target is a 400. A target that was allowed but did not answer
    keeps the previous behaviour and yields a minimal response carrying just
    the URL, so a slow or broken site still produces a usable bookmark.
    """
    url = payload.url

    try:
        final_url, html = await _fetch_preview_html(url)
    except HTTPException:
        raise
    except Exception:
        return BookmarkResponse(url=url)

    title = _extract_og(html, "og:title") or _extract_title_tag(html)
    description = (
        _extract_og(html, "og:description") or _extract_meta_name(html, "description")
    )
    image = _safe_preview_asset(_extract_og(html, "og:image"), final_url)

    parsed_final = urlparse(final_url)
    # Rebuilt from hostname and port rather than netloc: a URL may carry
    # credentials, and those must not end up in an attribute the page renders.
    origin = f"https://{parsed_final.hostname}"
    if parsed_final.port:
        origin = f"{origin}:{parsed_final.port}"
    favicon = _safe_preview_asset("/favicon.ico", f"{origin}/")

    return BookmarkResponse(
        url=url,
        title=title,
        description=description,
        image=image,
        favicon=favicon,
    )


# ─── Drive-file move ──────────────────────────────────────────────────────────


class DriveFileMoveRequest(BaseModel):
    # Typed as UUID rather than str: these values used to be interpolated into
    # a filesystem path straight from the request body, so a value such as
    # "../../.." moved files outside the upload tree. Pydantic now rejects
    # anything that is not a UUID with a 422 before the handler runs.
    file_uuid: uuid.UUID
    source_block_id: uuid.UUID
    target_block_id: uuid.UUID


class DriveFileMoveResponse(BaseModel):
    url: str


@media_router.post("/drive-file/move", response_model=DriveFileMoveResponse)
async def move_drive_file(
    payload: DriveFileMoveRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> DriveFileMoveResponse:
    """
    Physically move a file from one drive block's directory to another and
    return its new public URL.

    The caller is responsible for updating the block content JSON (removing
    the file from the source block and adding it—with the new URL—to the
    target block) via the blocks API after this endpoint confirms success.

    Both blocks are authorized: reading the file out of the source and writing
    it into the target are separate accesses, and permitting one does not
    imply the other.

    Raises 404 if the file is not found in the source directory.
    """
    require_block_access(db, payload.source_block_id, user)
    require_block_access(db, payload.target_block_id, user)

    source_block_id = str(payload.source_block_id)
    target_block_id = str(payload.target_block_id)
    file_uuid = str(payload.file_uuid)

    source_dir = _within_root(STATIC_ROOT / "drives" / source_block_id)
    target_dir = _within_root(STATIC_ROOT / "drives" / target_block_id)

    matches = list(source_dir.glob(f"{file_uuid}*"))
    if not matches:
        raise HTTPException(status_code=404, detail="File not found in source drive")

    src_file = matches[0]
    target_dir.mkdir(parents=True, exist_ok=True)
    dest_path = _within_root(target_dir / src_file.name)
    shutil.move(str(src_file), str(dest_path))

    return DriveFileMoveResponse(
        url=_public_url("drive", target_block_id, src_file.name),
    )
