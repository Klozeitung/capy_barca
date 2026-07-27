"""
Shared upload handling.

One implementation of the three things every upload path has to do: work out
the file's extension, decide whether the target accepts that type, and get the
bytes onto disk without pulling the whole body into memory first.

This lives in its own module rather than in the media router because it has
three callers, which had grown three versions of the same loop between them:

* ``app.media.router`` uploads a multipart file.
* ``app.blocks.router`` uploads a page cover, and its version inherited none of
  the hardening — no type allowlist, and a single ``read()`` of the entire body.
* ``app.wopi.router`` saves a document Collabora sends back, reading a raw
  request body rather than a multipart part.

The last of those is why the ceiling lives in ``write_stream``, which takes any
async iterable of chunks. ``stream_to_disk`` is the multipart adapter on top of
it, so there is one place the limit is enforced and one place it can be wrong.
"""
import mimetypes
import os
import uuid
from pathlib import Path
from typing import AsyncIterable, Optional

from fastapi import HTTPException, UploadFile

# The ceiling is enforced here as well as in nginx. nginx stops an oversized
# body at the edge; this stops one that arrives by any other route and produces
# a JSON answer the frontend can present, instead of nginx's HTML error page.
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_MB", "100")) * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024

TOO_LARGE = "The file exceeds the upload size limit"
TYPE_REFUSED = "This file type is not allowed for this block"

# Extensions the four media categories accept. The list is deliberately short
# and excludes SVG: an SVG is a document that can carry script, and an image
# block renders its source inline.
#
# The 'file' and 'drive' categories take arbitrary attachments and are absent
# here on purpose. What makes them safe is the delivery side, where anything
# outside INLINE_MEDIA_TYPES in app/main.py is handed out as a download with
# a neutral content type.
CATEGORY_EXTENSIONS: dict[str, frozenset[str]] = {
    "image": frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".bmp"}),
    "video": frozenset({".mp4", ".webm", ".ogv", ".mov", ".m4v"}),
    "audio": frozenset({".mp3", ".wav", ".ogg", ".oga", ".m4a", ".flac", ".aac"}),
    "pdf": frozenset({".pdf"}),
}

# A page cover is an image and is held to the image list. Named rather than
# spelled out at the call site so the two cannot drift.
COVER_CATEGORY = "image"


def resolve_extension(file: UploadFile) -> str:
    """
    Return the extension to store *file* under, lowercased and dot-prefixed.

    The uploaded filename decides, with the declared content type as a fallback
    when the name carries no suffix. Taking the name first keeps a ``.jpeg``
    stored as ``.jpeg``; deriving from the content type alone would rewrite it
    to ``.jpg``, because that is what ``mimetypes`` hands back for
    ``image/jpeg``, and every URL already issued for such a file would break.

    Neither source is trusted on its own. What makes the result safe is
    ``assert_type_permitted`` refusing anything outside a fixed list.
    """
    ext = Path(file.filename or "").suffix.lower()
    if not ext and file.content_type:
        ext = mimetypes.guess_extension(file.content_type) or ""
    return ext


def assert_type_permitted(ext: str, category: str) -> None:
    """
    Raise 415 unless *ext* is one of the extensions *category* accepts.

    A category with no entry in ``CATEGORY_EXTENSIONS`` accepts anything. That
    is deliberate for 'file' and 'drive', which exist to hold arbitrary
    attachments; they are made safe by how they are served, not by what may be
    stored.
    """
    permitted = CATEGORY_EXTENSIONS.get(category)
    if permitted is not None and ext not in permitted:
        raise HTTPException(status_code=415, detail=TYPE_REFUSED)


async def write_stream(
    chunks: AsyncIterable[bytes],
    dest_path: Path,
    *,
    max_bytes: Optional[int] = None,
) -> int:
    """
    Write an async stream of *chunks* to *dest_path*, returning the bytes stored.

    Written incrementally rather than read whole: a single read pulls the entire
    body into memory before anything is checked, so the size limit would arrive
    after the damage. Streaming also lets the ceiling stop the write at the
    moment it is crossed.

    A body past the ceiling raises 413 and the partial file is removed first, so
    a refused write leaves nothing behind.

    ``max_bytes`` falls back to ``MAX_UPLOAD_BYTES`` read at call time rather
    than bound as a default argument value, so patching the module attribute
    actually changes the ceiling.
    """
    ceiling = MAX_UPLOAD_BYTES if max_bytes is None else max_bytes

    size = 0
    too_large = False
    with dest_path.open("wb") as sink:
        async for chunk in chunks:
            size += len(chunk)
            if size > ceiling:
                too_large = True
                break
            sink.write(chunk)

    if too_large:
        dest_path.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail=TOO_LARGE)

    return size


async def _read_in_chunks(file: UploadFile) -> AsyncIterable[bytes]:
    """Adapt an UploadFile to the chunk stream ``write_stream`` consumes."""
    while chunk := await file.read(UPLOAD_CHUNK_BYTES):
        yield chunk


async def stream_to_disk(
    file: UploadFile,
    dest_path: Path,
    *,
    max_bytes: Optional[int] = None,
) -> int:
    """
    Write an uploaded *file* to *dest_path* and return the number of bytes stored.

    The multipart entry point to ``write_stream``; the ceiling, the 413 and the
    removal of a partial file all happen there.
    """
    return await write_stream(
        _read_in_chunks(file), dest_path, max_bytes=max_bytes
    )


def staging_path(directory: Path) -> Path:
    """
    Return an unused path in *directory* to stream a partial upload into.

    Callers that replace an existing file use this so a refused body does not
    cost the caller what they already had: the new bytes land beside the target
    and are moved over it only once the whole upload has been accepted.

    The name deliberately does not start with the final file's stem. The cover
    cleanup globs on that stem, and a partial file it could match would be a
    file it could delete out from under a concurrent upload.
    """
    return directory / f".upload-{uuid.uuid4().hex}.part"
