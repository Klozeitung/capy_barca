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
"""
import mimetypes
import re
import shutil
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Cookie, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.session.session import validate_token

# Root for all uploaded files. Always the default static/uploads directory.
STATIC_ROOT: Path = Path("static/uploads")

MEDIA_CATEGORIES: frozenset[str] = frozenset({"image", "video", "audio", "pdf"})
VALID_CATEGORIES: frozenset[str] = MEDIA_CATEGORIES | frozenset({"file", "drive"})

media_router = APIRouter(prefix="/api/media", tags=["media"])


# ─── Auth ─────────────────────────────────────────────────────────────────────


def _require_session(session: Optional[str] = Cookie(default=None)) -> str:
    if not session or not validate_token(session):
        raise HTTPException(status_code=401, detail="Nicht angemeldet")
    return session


# ─── Internal helpers ─────────────────────────────────────────────────────────


def _storage_dir(category: str, block_id: str) -> Path:
    """Return the storage directory for a given category and block_id."""
    if category in MEDIA_CATEGORIES:
        return STATIC_ROOT / "media" / category
    if category == "file":
        return STATIC_ROOT / "files"
    # category == "drive"
    return STATIC_ROOT / "drives" / block_id


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
    _session: str = Depends(_require_session),
) -> CapacityResponse:
    """
    Return disk capacity information for the upload storage directory.

    Reports the total, used, and free bytes of the filesystem partition that
    holds ``STATIC_ROOT``. The directory is created eagerly if it does not
    exist yet so that a fresh installation does not cause a 500.
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
    _session: str = Depends(_require_session),
) -> UploadResponse:
    """
    Upload a file and store it under the appropriate static sub-path.

    Returns the generated ``file_uuid``, public URL, original filename,
    byte size, and MIME type. The caller is responsible for persisting these
    values in the block's ``content`` field via the blocks API.
    """
    if category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Unknown category: {category!r}")

    block_id_str = str(block_id)
    file_uuid = str(uuid.uuid4())
    original_name = file.filename or "upload"
    ext = Path(original_name).suffix.lower()
    if not ext and file.content_type:
        ext = mimetypes.guess_extension(file.content_type) or ""

    stored_name = f"{file_uuid}{ext}"
    dest_dir = _storage_dir(category, block_id_str)
    dest_dir.mkdir(parents=True, exist_ok=True)

    contents = await file.read()
    (dest_dir / stored_name).write_bytes(contents)

    return UploadResponse(
        file_uuid=file_uuid,
        url=_public_url(category, block_id_str, stored_name),
        filename=original_name,
        size=len(contents),
        mime=file.content_type or "application/octet-stream",
    )


@media_router.delete("/{category}/{block_id}/{file_uuid}", response_model=DeleteResponse)
async def delete_file(
    category: str,
    block_id: uuid.UUID,
    file_uuid: uuid.UUID,
    _session: str = Depends(_require_session),
) -> DeleteResponse:
    """Delete a previously uploaded file identified by its UUID."""
    if category not in VALID_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Unknown category: {category!r}")

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
async def fetch_bookmark(
    payload: BookmarkRequest,
    _session: str = Depends(_require_session),
) -> BookmarkResponse:
    """
    Fetch Open Graph / meta data for a URL to build a bookmark preview.

    On any network or parse error the endpoint returns a minimal response
    with just the URL rather than propagating a 5xx to the client.
    """
    url = payload.url
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
            response = await client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; CapyBarca/1.0)"},
            )
            html = response.text
    except Exception:
        return BookmarkResponse(url=url)

    title = _extract_og(html, "og:title") or _extract_title_tag(html)
    description = (
        _extract_og(html, "og:description") or _extract_meta_name(html, "description")
    )
    image = _extract_og(html, "og:image")

    parsed = urlparse(url)
    favicon = f"{parsed.scheme}://{parsed.netloc}/favicon.ico"

    return BookmarkResponse(
        url=url,
        title=title,
        description=description,
        image=image,
        favicon=favicon,
    )


# ─── Drive-file move ──────────────────────────────────────────────────────────


class DriveFileMoveRequest(BaseModel):
    file_uuid: str
    source_block_id: str
    target_block_id: str


class DriveFileMoveResponse(BaseModel):
    url: str


@media_router.post("/drive-file/move", response_model=DriveFileMoveResponse)
async def move_drive_file(
    payload: DriveFileMoveRequest,
    _session: str = Depends(_require_session),
) -> DriveFileMoveResponse:
    """
    Physically move a file from one drive block's directory to another and
    return its new public URL.

    The caller is responsible for updating the block content JSON (removing
    the file from the source block and adding it—with the new URL—to the
    target block) via the blocks API after this endpoint confirms success.

    Raises 404 if the file is not found in the source directory.
    """
    source_dir = STATIC_ROOT / "drives" / payload.source_block_id
    target_dir = STATIC_ROOT / "drives" / payload.target_block_id

    matches = list(source_dir.glob(f"{payload.file_uuid}*"))
    if not matches:
        raise HTTPException(status_code=404, detail="File not found in source drive")

    src_file = matches[0]
    target_dir.mkdir(parents=True, exist_ok=True)
    dest_path = target_dir / src_file.name
    shutil.move(str(src_file), str(dest_path))

    return DriveFileMoveResponse(
        url=_public_url("drive", payload.target_block_id, src_file.name),
    )
