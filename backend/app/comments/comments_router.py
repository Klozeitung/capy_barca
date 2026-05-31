"""
Comments router.

HTTP interface for block comments.  All endpoints require a valid session
cookie, enforced via the shared ``require_session`` / ``get_current_user``
dependencies from ``app.session.deps`` — the same mechanism used by every
router except ``app.blocks.router``.

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
from app.session.deps import get_current_user, get_db, require_session
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

    model_config = {"from_attributes": True}


# ─── Internal helpers ─────────────────────────────────────────────────────────


def _block_exists(db: Session, block_id: uuid.UUID) -> bool:
    """Return True when a block with the given ID exists in the blocks table."""
    from app.blocks.models import Block  # local import to avoid circular deps

    return db.get(Block, block_id) is not None


def _serialize(comment: Comment) -> CommentResponse:
    return CommentResponse(
        id=comment.id,
        block_id=comment.block_id,
        author_id=comment.author_id,
        text=comment.text,
        created_at=comment.created_at.replace(tzinfo=timezone.utc).isoformat(),
        updated_at=comment.updated_at.replace(tzinfo=timezone.utc).isoformat(),
    )


# ─── Endpoints ────────────────────────────────────────────────────────────────


@comments_router.get(
    "/{block_id}/comments",
    response_model=list[CommentResponse],
)
def list_comments(
    block_id: uuid.UUID,
    db: Session = Depends(get_db),
    _user_id: uuid.UUID = Depends(require_session),
):
    """Return all comments for *block_id* ordered by creation time (oldest first)."""
    if not _block_exists(db, block_id):
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
    return [_serialize(c) for c in rows]


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
    comment = Comment(
        id=uuid.uuid4(),
        block_id=block_id,
        author_id=current_user.id,
        text=payload.text.strip(),
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return _serialize(comment)


@comments_router.patch(
    "/{block_id}/comments/{comment_id}",
    response_model=CommentResponse,
)
def update_comment(
    block_id: uuid.UUID,
    comment_id: uuid.UUID,
    payload: CommentUpdate,
    db: Session = Depends(get_db),
    _user_id: uuid.UUID = Depends(require_session),
):
    """Edit the text of an existing comment."""
    comment = db.get(Comment, comment_id)
    if comment is None or comment.block_id != block_id:
        raise HTTPException(status_code=404, detail=f"Comment {comment_id} not found")
    comment.text = payload.text.strip()
    db.flush()
    db.commit()
    db.refresh(comment)
    return _serialize(comment)


@comments_router.delete(
    "/{block_id}/comments/{comment_id}",
    status_code=204,
)
def delete_comment(
    block_id: uuid.UUID,
    comment_id: uuid.UUID,
    db: Session = Depends(get_db),
    _user_id: uuid.UUID = Depends(require_session),
):
    """Permanently delete a comment."""
    comment = db.get(Comment, comment_id)
    if comment is None or comment.block_id != block_id:
        raise HTTPException(status_code=404, detail=f"Comment {comment_id} not found")
    db.delete(comment)
    db.commit()
