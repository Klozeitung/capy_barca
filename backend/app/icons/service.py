"""
Uploaded icons: storing, listing and deleting them.

Storage
-------
Files land in ``static/uploads/icons/{id}{ext}``, beside the other uploads, so
the backup covers them and the hardened static delivery in ``app.main`` serves
them with a type chosen from the extension. Only raster formats are accepted
(PNG, JPEG, GIF, WebP), for the reason the media uploads exclude SVG: an SVG is
a document that can carry script. The content has to start with the signature
of the format its extension names, so a renamed file of another kind is
refused. The size limit is far below the general upload limit, because an icon
is drawn at a few dozen pixels.

Deleting
--------
An icon may be deleted while it is in use. The places that referenced it are
updated in the same transaction:

* A database whose default entry icon it was loses its default.
* An entry of a database gets the default entry icon of its database, or no
  icon when the database has none (the display then falls back to the generic
  entry icon).
* Every other block, entry templates included, loses its icon.
* The recently used icons of every account lose it.

Relation chips, formulas and rollups that show a changed entry's icon are
recomputed, and the changes are reported the way ``PATCH
/api/blocks/{id}/appearance`` reports them, once the transaction has
committed (see ``CleanupResult``). Callout blocks keep the icon in their
content; the web app shows its default callout icon for an icon that no
longer exists.
"""
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, UploadFile
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.blocks import events as block_events
from app.blocks.computed import compute_cross_db_dependents, compute_same_db_rollup_dependents
from app.blocks.models import Block, DatabaseSettings
from app.icons.model import CustomIcon
from app.icons.names import custom_icon_name
from app.media import upload as upload_helper
from app.users.model import User

# Root of all uploads; icons live in their own directory below it.
STATIC_ROOT: Path = Path("static/uploads")
ICONS_DIRECTORY = "icons"

# An icon is shown at a few dozen pixels; anything larger is a mistake.
MAX_ICON_BYTES = 512 * 1024

# Longest name the picker shows for an icon.
NAME_MAX_LENGTH = 64

TOO_LARGE = "The icon exceeds the size limit"
TYPE_REFUSED = "Only PNG, JPEG, GIF and WebP images can be icons"

# Accepted extensions and the signatures their content has to start with.
_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"),
}
ACCEPTED_EXTENSIONS: frozenset[str] = frozenset({*_SIGNATURES, ".webp"})

_WHITESPACE = re.compile(r"\s+")


# ─── Paths ────────────────────────────────────────────────────────────────────


def icons_dir() -> Path:
    """Return the directory icon files are stored in."""
    return STATIC_ROOT / ICONS_DIRECTORY


def icon_url(icon: CustomIcon) -> str:
    """Return the URL the icon's file is served at."""
    return f"/static/uploads/{ICONS_DIRECTORY}/{icon.stored_name}"


def _icon_path(stored_name: str) -> Path:
    """
    Return the path of *stored_name*, refusing one outside the icon directory.

    Stored names are built from a UUID and a fixed extension, so this cannot
    trigger today; it guards a delete against a future mistake.
    """
    directory = icons_dir().resolve()
    path = (icons_dir() / stored_name).resolve()
    if path.parent != directory:
        raise HTTPException(status_code=400, detail="Invalid storage path")
    return path


# ─── Upload ───────────────────────────────────────────────────────────────────


def display_name(filename: Optional[str]) -> str:
    """
    Return the name the picker shows for an icon uploaded as *filename*: the
    file name without directory and extension, whitespace collapsed, cut to
    ``NAME_MAX_LENGTH`` characters. Falls back to ``"icon"``.
    """
    stem = Path((filename or "").replace("\\", "/")).stem
    name = _WHITESPACE.sub(" ", stem).strip()[:NAME_MAX_LENGTH].strip()
    return name or "icon"


def _matches_signature(path: Path, ext: str) -> bool:
    """True when the content of *path* starts like a file of type *ext*."""
    with path.open("rb") as stored:
        head = stored.read(12)
    if ext == ".webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    return any(head.startswith(signature) for signature in _SIGNATURES[ext])


def store_icon(db: Session, file: UploadFile, uploaded_by: uuid.UUID) -> CustomIcon:
    """
    Store an uploaded image as a new icon and return it.

    Refuses other types with 415 and files above ``MAX_ICON_BYTES`` with 413;
    a refused upload leaves no file behind. The caller commits.
    """
    ext = upload_helper.resolve_extension(file)
    if ext not in ACCEPTED_EXTENSIONS:
        raise HTTPException(status_code=415, detail=TYPE_REFUSED)

    directory = icons_dir()
    directory.mkdir(parents=True, exist_ok=True)
    icon_id = uuid.uuid4()
    stored_name = f"{icon_id}{ext}"
    staging = upload_helper.staging_path(directory)
    try:
        try:
            upload_helper.save_upload(file, staging, max_bytes=MAX_ICON_BYTES)
        except HTTPException as exc:
            if exc.status_code == 413:
                raise HTTPException(status_code=413, detail=TOO_LARGE) from exc
            raise
        if not _matches_signature(staging, ext):
            raise HTTPException(status_code=415, detail=TYPE_REFUSED)
        staging.replace(directory / stored_name)
    finally:
        staging.unlink(missing_ok=True)

    icon = CustomIcon(
        id=icon_id,
        name=display_name(file.filename),
        stored_name=stored_name,
        uploaded_by=uploaded_by,
    )
    db.add(icon)
    db.flush()
    return icon


def list_icons(db: Session) -> list[CustomIcon]:
    """Return every uploaded icon, oldest first."""
    return list(db.scalars(
        select(CustomIcon).order_by(CustomIcon.created_at, CustomIcon.id)
    ))


# ─── Delete ───────────────────────────────────────────────────────────────────


@dataclass
class CleanupResult:
    """
    What deleting an icon changed, for the notifications sent after the
    commit.

    ``blocks`` maps each block whose icon changed to its state before the
    change (``id`` and ``icon``); ``databases`` lists the databases whose
    entries now differ, for ``database_entries_updated``.
    """

    blocks: dict[uuid.UUID, Optional[str]] = field(default_factory=dict)
    databases: list[str] = field(default_factory=list)


def delete_icon(db: Session, icon: CustomIcon) -> CleanupResult:
    """
    Delete *icon* and replace it wherever it is referenced (see the module
    docstring). The file is removed by ``remove_file`` once the caller has
    committed, so a rollback leaves the icon intact. The caller commits.
    """
    name = custom_icon_name(icon.id)
    result = CleanupResult()

    # Defaults first, so entries do not fall back to the icon being removed.
    db.execute(
        update(DatabaseSettings)
        .where(DatabaseSettings.default_entry_icon == name)
        .values(default_entry_icon=None)
    )
    db.flush()

    blocks = list(db.scalars(select(Block).where(Block.icon == name)))
    parent_ids = {b.parent_id for b in blocks if b.parent_id is not None}
    databases = {
        b.id for b in db.scalars(
            select(Block).where(Block.id.in_(parent_ids), Block.type == "database")
        )
    } if parent_ids else set()
    defaults = dict(db.execute(
        select(DatabaseSettings.database_id, DatabaseSettings.default_entry_icon)
        .where(DatabaseSettings.database_id.in_(databases))
    ).all()) if databases else {}

    for block in blocks:
        is_entry = block.parent_id in databases and block.type != "entry_template"
        replacement = defaults.get(block.parent_id) if is_entry else None
        result.blocks[block.id] = block.icon
        block.icon = replacement
        block_events.emit_icon_changed(db, block.id, name, replacement)
    db.flush()

    for block in blocks:
        if block.parent_id not in databases or block.type == "entry_template":
            continue
        database_id = str(block.parent_id)
        if database_id not in result.databases:
            result.databases.append(database_id)
        compute_same_db_rollup_dependents(db, block.parent_id, block.id)
        for dependent in compute_cross_db_dependents(db, block.parent_id, block.id):
            if str(dependent) not in result.databases:
                result.databases.append(str(dependent))

    _forget_recent_use(db, name)
    db.delete(icon)
    db.flush()
    return result


def _forget_recent_use(db: Session, name: str) -> None:
    """Remove *name* from the recently used icons of every account."""
    users = db.scalars(
        select(User).where(User.recent_icons.contains([{"icon": name}])).with_for_update()
    )
    for user in users:
        user.recent_icons = [e for e in (user.recent_icons or []) if e.get("icon") != name]


def remove_file(icon: CustomIcon) -> None:
    """Delete the file of *icon*; a file that is already gone is fine."""
    _icon_path(icon.stored_name).unlink(missing_ok=True)
