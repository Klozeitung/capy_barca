"""
Comments router.

HTTP interface for block comments.  All endpoints resolve the caller through
``get_current_user`` from ``app.session.deps``, the same gate every router
uses.

Authorization
-------------
Two separate questions, asked in that order:

1. May the caller reach the block at all? ``require_block_access`` answers it,
   and every endpoint here asks. A comment thread is block content.
2. May the caller change *this* comment? Editing and deleting additionally
   require being the comment's author, or an admin. Anyone with block access
   may read and create.

``author_id`` is nullable, so a comment whose author has since been deleted
degrades to admin-only rather than becoming editable by everyone.

Listing answers 404 for a block the caller may not see, so it stays
indistinguishable from a block that does not exist. Writes answer 403, because
reaching them requires already knowing the id. This mirrors the block router.

Endpoints
---------
GET    /api/blocks/{block_id}/comments          – list all comments for a block
POST   /api/blocks/{block_id}/comments          – create a new comment
PATCH  /api/blocks/{block_id}/comments/{id}     – edit comment text
DELETE /api/blocks/{block_id}/comments/{id}     – delete a comment
"""
import uuid
from datetime import timezone
from typing import Optional

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.comments.comments_models import Comment
from app.permissions import repository as perm_repo
from app.session.deps import get_current_user, get_db, require_block_access
from app.users.model import User

comments_router = APIRouter(prefix="/api/blocks", tags=["comments"])


# ─── Request / Response schemas ───────────────────────────────────────────────


class CommentCreate(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def text_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Comment text must not be empty")
        return v


class CommentUpdate(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def text_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Comment text must not be empty")
        return v


class CommentResponse(BaseModel):
    id: uuid.UUID
    block_id: uuid.UUID
    author_id: Optional[uuid.UUID]
    text: str
    created_at: str
    updated_at: str
    # Whether the requesting account may edit or delete this comment. Computed
    # server-side so the client does not have to reimplement the rule, and so a
    # client that gets it wrong cannot grant itself anything.
    can_edit: bool

    model_config = {"from_attributes": True}


# ─── Internal helpers ─────────────────────────────────────────────────────────


def _block_exists(db: Session, block_id: uuid.UUID) -> bool:
    """Return True when a block with the given ID exists in the blocks table."""
    from app.blocks.models import Block  # local import to avoid circular deps

    return db.get(Block, block_id) is not None


def _may_modify(comment: Comment, user: User) -> bool:
    """
    Return whether *user* may edit or delete *comment*.

    Block access is a separate question, answered before this one. An unowned
    comment — author deleted — is admin-only rather than open to all.
    """
    if user.role == "admin":
        return True
    return comment.author_id is not None and comment.author_id == user.id


def _require_modify(comment: Comment, user: User) -> None:
    """Raise 403 unless *user* may modify *comment*."""
    if not _may_modify(comment, user):
        raise HTTPException(status_code=403, detail="Not the author of this comment")


def _serialize(comment: Comment, user: User) -> CommentResponse:
    return CommentResponse(
        id=comment.id,
        block_id=comment.block_id,
        author_id=comment.author_id,
        text=comment.text,
        created_at=comment.created_at.replace(tzinfo=timezone.utc).isoformat(),
        updated_at=comment.updated_at.replace(tzinfo=timezone.utc).isoformat(),
        can_edit=_may_modify(comment, user),
    )


# ─── Endpoints ────────────────────────────────────────────────────────────────


@comments_router.get(
    "/{block_id}/comments",
    response_model=list[CommentResponse],
)
def list_comments(
    block_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all comments for *block_id* ordered by creation time (oldest first)."""
    # 404 rather than 403: a read must not confirm that a block the caller may
    # not see exists at all.
    if not _block_exists(db, block_id) or not perm_repo.can_user_access(
        db, block_id, current_user
    ):
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
    rows = (
        db.execute(
            sa.select(Comment)
            .where(Comment.block_id == block_id)
            .order_by(Comment.created_at.asc())
        )
        .scalars()
        .all()
    )
    return [_serialize(c, current_user) for c in rows]


@comments_router.post(
    "/{block_id}/comments",
    response_model=CommentResponse,
    status_code=201,
)
def create_comment(
    block_id: uuid.UUID,
    payload: CommentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new comment on *block_id*, attributed to the current user."""
    if not _block_exists(db, block_id):
        raise HTTPException(status_code=404, detail=f"Block {block_id} not found")
    require_block_access(db, block_id, current_user)
    comment = Comment(
        id=uuid.uuid4(),
        block_id=block_id,
        author_id=current_user.id,
        text=payload.text.strip(),
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return _serialize(comment, current_user)


@comments_router.patch(
    "/{block_id}/comments/{comment_id}",
    response_model=CommentResponse,
)
def update_comment(
    block_id: uuid.UUID,
    comment_id: uuid.UUID,
    payload: CommentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Edit the text of an existing comment. Author or admin only."""
    # Block access first: whether a comment exists on an unreachable block is
    # not something the caller gets to find out.
    require_block_access(db, block_id, current_user)
    comment = db.get(Comment, comment_id)
    if comment is None or comment.block_id != block_id:
        raise HTTPException(status_code=404, detail=f"Comment {comment_id} not found")
    _require_modify(comment, current_user)
    comment.text = payload.text.strip()
    db.flush()
    db.commit()
    db.refresh(comment)
    return _serialize(comment, current_user)


@comments_router.delete(
    "/{block_id}/comments/{comment_id}",
    status_code=204,
)
def delete_comment(
    block_id: uuid.UUID,
    comment_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Permanently delete a comment. Author or admin only."""
    require_block_access(db, block_id, current_user)
    comment = db.get(Comment, comment_id)
    if comment is None or comment.block_id != block_id:
        raise HTTPException(status_code=404, detail=f"Comment {comment_id} not found")
    _require_modify(comment, current_user)
    db.delete(comment)
    db.commit()
