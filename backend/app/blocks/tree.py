"""
Page tree loading.

A page renders more than its direct children: layouts hold columns, columns
and toggles hold blocks of their own, and a synced mirror shows the children
of its origin. Fetching each of those containers separately costs one request
per container, issued level by level as the client discovers them.

:func:`load_tree` returns everything a page shows in one pass instead. It
walks the containers one level at a time, so the number of queries depends on
how deeply the content is nested, not on how many blocks it holds. Subpages,
databases and linked databases are not entered: they are separate documents
that load their own content when opened.

The blocks referenced by inline mentions in the loaded text are returned as
well, so mention chips can show their titles without asking for each one.

Permissions follow the children endpoint. A block the caller may not read is
left out together with everything beneath it, and a mirror whose origin the
caller may not read is not expanded.
"""
import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.blocks.models import Block
from app.permissions import repository as perm_repo
from app.users.model import User

# Blocks whose children are rendered inline on the page they belong to,
# whether currently folded or not.
CONTAINER_TYPES: frozenset[str] = frozenset({
    "toggle",
    "text_toggle",
    "heading_1_toggle",
    "heading_2_toggle",
    "heading_3_toggle",
    "heading_4_toggle",
    "layout",
    "column",
    "synched_origin",
})

# Renders the children of the block its ``reference_id`` points to.
MIRROR_TYPE = "synched_mirror"

# Nesting levels walked at most. Deeper content still loads, lazily, through
# the children endpoint when the client finds a container it has not loaded.
MAX_LEVELS = 32

# Inline mention token as stored in ``content.text``: ``@<uuid>|``.
MENTION_RE = re.compile(
    r"@([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\|"
)


@dataclass
class PageTree:
    """
    Result of :func:`load_tree`.

    ``blocks`` holds the loaded blocks ordered by parent, then by position.
    ``loaded`` names every parent whose readable active children are all in
    ``blocks``; a parent listed there without children in ``blocks`` has none.
    ``mentioned`` holds the readable blocks that inline mentions refer to and
    that are not part of the tree already.
    """

    blocks: list[Block] = field(default_factory=list)
    loaded: list[uuid.UUID] = field(default_factory=list)
    mentioned: list[Block] = field(default_factory=list)


def mention_ids(blocks: list[Block]) -> list[uuid.UUID]:
    """Return the ids mentioned in the text of *blocks*, first occurrence first."""
    found: dict[uuid.UUID, None] = {}
    for block in blocks:
        text = (block.content or {}).get("text")
        if not isinstance(text, str) or "@" not in text:
            continue
        for match in MENTION_RE.finditer(text):
            found.setdefault(uuid.UUID(match.group(1)), None)
    return list(found)


def _readable(db: Session, blocks: list[Block], user: User) -> list[Block]:
    """Keep the blocks of *blocks* that *user* may read, in their order."""
    allowed = perm_repo.filter_accessible(db, (b.id for b in blocks), user)
    return [b for b in blocks if b.id in allowed]


def load_tree(db: Session, root_id: uuid.UUID, user: User) -> PageTree:
    """
    Load the content of *root_id* as the page renders it.

    The caller has already checked that *user* may read *root_id*. Every
    other block is checked here, one batched permission walk per level.
    """
    tree = PageTree()
    expanded: set[uuid.UUID] = {root_id}
    frontier: list[uuid.UUID] = [root_id]

    for _ in range(MAX_LEVELS):
        if not frontier:
            break
        children = list(db.scalars(
            select(Block)
            .where(Block.parent_id.in_(frontier), Block.state == "active")
            .order_by(Block.parent_id, Block.position)
        ))
        tree.loaded.extend(frontier)
        visible = _readable(db, children, user)
        tree.blocks.extend(visible)

        next_frontier: list[uuid.UUID] = []
        origins: list[uuid.UUID] = []
        for block in visible:
            if block.type in CONTAINER_TYPES and block.id not in expanded:
                expanded.add(block.id)
                next_frontier.append(block.id)
            elif (
                block.type == MIRROR_TYPE
                and block.reference_id is not None
                and block.reference_id not in expanded
            ):
                expanded.add(block.reference_id)
                origins.append(block.reference_id)

        if origins:
            existing = list(db.scalars(select(Block.id).where(Block.id.in_(origins))))
            allowed = perm_repo.filter_accessible(db, existing, user)
            next_frontier.extend(o for o in origins if o in allowed)
        frontier = next_frontier

    known = {root_id, *(b.id for b in tree.blocks)}
    wanted = [i for i in mention_ids(tree.blocks) if i not in known]
    if wanted:
        found = list(db.scalars(select(Block).where(Block.id.in_(wanted))))
        by_id = {b.id: b for b in _readable(db, found, user)}
        tree.mentioned = [by_id[i] for i in wanted if i in by_id]
    return tree
