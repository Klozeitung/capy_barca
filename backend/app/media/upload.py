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

The ceiling is enforced in one place, ``_CappedWriter``, which both entry
points share:

* ``save_upload`` writes a multipart file. By the time a handler runs,
  Starlette has already spooled the part into a temporary file, so reading it
  is plain blocking file I/O; it is meant for synchronous handlers, which run
  in a worker thread.
* ``write_stream`` consumes an async iterable of chunks, for the WOPI save,
  whose body arrives as a raw stream that is still being received.
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


class _CappedWriter:
    """
    Writes chunks to a file and stops at the upload ceiling.

    Written incrementally rather than read whole: a single read pulls the entire
    body into memory before anything is checked, so the size limit would arrive
    after the damage. Streaming also lets the ceiling stop the write at the
    moment it is crossed.

    The ceiling falls back to ``MAX_UPLOAD_BYTES`` read at construction time
    rather than bound as a default argument value, so patching the module
    attribute actually changes it.
    """

    def __init__(self, dest_path: Path, max_bytes: Optional[int]) -> None:
        self.dest_path = dest_path
        self.ceiling = MAX_UPLOAD_BYTES if max_bytes is None else max_bytes
        self.size = 0
        self.too_large = False

    def __enter__(self) -> "_CappedWriter":
        self._sink = self.dest_path.open("wb")
        return self

    def __exit__(self, *exc_info) -> None:
        self._sink.close()

    def write(self, chunk: bytes) -> bool:
        """Write *chunk*; return False, writing nothing, once the ceiling is crossed."""
        self.size += len(chunk)
        if self.size > self.ceiling:
            self.too_large = True
            return False
        self._sink.write(chunk)
        return True

    def finish(self) -> int:
        """
        Return the bytes stored, or refuse an oversized body.

        A body past the ceiling raises 413 and the partial file is removed
        first, so a refused write leaves nothing behind.
        """
        if self.too_large:
            self.dest_path.unlink(missing_ok=True)
            raise HTTPException(status_code=413, detail=TOO_LARGE)
        return self.size


async def write_stream(
    chunks: AsyncIterable[bytes],
    dest_path: Path,
    *,
    max_bytes: Optional[int] = None,
) -> int:
    """
    Write an async stream of *chunks* to *dest_path*, returning the bytes stored.

    For bodies that are still arriving while they are written. Raises 413 past
    the ceiling, see ``_CappedWriter``.
    """
    with _CappedWriter(dest_path, max_bytes) as writer:
        async for chunk in chunks:
            if not writer.write(chunk):
                break
    return writer.finish()


def save_upload(
    file: UploadFile,
    dest_path: Path,
    *,
    max_bytes: Optional[int] = None,
) -> int:
    """
    Write an uploaded *file* to *dest_path* and return the number of bytes stored.

    Synchronous: the multipart part is already spooled to a temporary file,
    so this is ordinary file I/O and belongs in a worker thread, where
    synchronous request handlers run. Raises 413 past the ceiling, see
    ``_CappedWriter``.
    """
    with _CappedWriter(dest_path, max_bytes) as writer:
        while chunk := file.file.read(UPLOAD_CHUNK_BYTES):
            if not writer.write(chunk):
                break
    return writer.finish()


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
