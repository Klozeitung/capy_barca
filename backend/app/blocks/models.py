import uuid
from datetime import datetime
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.database import Base

WORKSPACE_ROOT_ID: uuid.UUID = uuid.UUID("00000000-0000-0000-0000-000000000001")
"""
Fixed UUID of the single workspace root block.

This block is inserted as a seed in migration 0002 and serves as the
top-level parent for all user-created content. Its ID is stable across
all installations, making it safe to reference in application code.
"""


class Block(Base):
    """
    Universal content unit.

    Every piece of content in CapyBarca – the workspace root, pages,
    databases, database views, paragraphs, headings, and all other editor
    primitives – is a Block. The ``type`` column carries the semantic role;
    the application layer enforces all type-specific rules and constraints.

    Hierarchy is expressed through ``parent_id`` (parent/child) and
    ``position`` (sibling order via fractional indexing). ``reference_id``
    is populated only on reference blocks such as ``database_view``, where
    it points to the source database block.

    ``icon`` stores an Iconify icon string, e.g. ``"mdi:file-document"``.
    ``cover`` stores either an image URL (``https://...``) or a CSS gradient
    prefixed with ``gradient:``, e.g. ``"gradient:linear-gradient(...)"`` .

    Deletion is soft: ``state`` transitions from ``active`` to ``trash``.
    Hard deletion is performed by an explicit purge operation which relies
    on the ``ON DELETE CASCADE`` constraint to remove the full subtree.
    """

    __tablename__ = "blocks"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, primary_key=True, default=uuid.uuid4
    )
    parent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("blocks.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
        default=None,
    )
    reference_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("blocks.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    position: Mapped[float] = mapped_column(sa.Float, nullable=False)
    state: Mapped[str] = mapped_column(
        sa.Text, nullable=False, default="active", server_default="active"
    )
    content: Mapped[Optional[Any]] = mapped_column(sa.JSON, nullable=True, default=None)
    icon: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True, default=None)
    cover: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True, default=None)
    # Owner of this block.  No FK so that deleting a user does not affect
    # the block tree.  Set to the creating user at block creation time.
    # Backfilled to the first active admin for all pre-existing rows by
    # migration 0015.
    owner_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        sa.Uuid, nullable=True, default=None, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )

    # Self-referential parent/child relationship.
    # foreign_keys disambiguates between parent_id and reference_id,
    # both of which point to blocks.id.
    children: Mapped[list["Block"]] = relationship(
        "Block",
        back_populates="parent",
        foreign_keys=[parent_id],
        lazy="select",
    )
    parent: Mapped[Optional["Block"]] = relationship(
        "Block",
        back_populates="children",
        foreign_keys=[parent_id],
        remote_side=[id],
        lazy="select",
    )

    property_schemas: Mapped[list["PropertySchema"]] = relationship(
        "PropertySchema",
        back_populates="database_block",
        cascade="all, delete-orphan",
        lazy="select",
    )
    property_values: Mapped[list["PropertyValue"]] = relationship(
        "PropertyValue",
        back_populates="page_block",
        cascade="all, delete-orphan",
        lazy="select",
    )
    preferences: Mapped[list["BlockPreference"]] = relationship(
        "BlockPreference",
        back_populates="block",
        cascade="all, delete-orphan",
        lazy="select",
    )
    events: Mapped[list["BlockEvent"]] = relationship(
        "BlockEvent",
        back_populates="block",
        lazy="select",
        # No cascade delete: events survive block purge (block_id → NULL).
    )


class PropertySchema(Base):
    """
    Defines a typed property column owned by a database block.

    The ``(database_id, name)`` unique constraint is intentional and load-
    bearing: it is the basis for name-based matching when an entry block is
    moved between databases (see service layer).

    ``config`` carries type-specific metadata as JSONB, e.g. the list of
    allowed options for a ``select`` property or the target database UUID
    for a ``relation`` property.
    """

    __tablename__ = "property_schemas"
    __table_args__ = (
        sa.UniqueConstraint(
            "database_id", "name", name="uq_property_schemas_database_name"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, primary_key=True, default=uuid.uuid4
    )
    database_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("blocks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    config: Mapped[Optional[Any]] = mapped_column(sa.JSON, nullable=True, default=None)
    position: Mapped[float] = mapped_column(sa.Float, nullable=False)
    group: Mapped[str] = mapped_column(
        sa.Text, nullable=False, default="Standard", server_default="Standard"
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )

    database_block: Mapped["Block"] = relationship(
        "Block",
        back_populates="property_schemas",
        lazy="select",
    )
    values: Mapped[list["PropertyValue"]] = relationship(
        "PropertyValue",
        back_populates="schema",
        cascade="all, delete-orphan",
        lazy="select",
    )


class PropertyValue(Base):
    """
    Stores the value an entry block holds for a specific property schema.

    The ``(page_id, property_schema_id)`` unique constraint ensures each
    entry carries at most one value per property. ``value`` is JSONB to
    accommodate all property types uniformly without a per-type column
    explosion.
    """

    __tablename__ = "property_values"
    __table_args__ = (
        sa.UniqueConstraint(
            "page_id", "property_schema_id", name="uq_property_values_page_schema"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, primary_key=True, default=uuid.uuid4
    )
    page_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("blocks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    property_schema_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("property_schemas.id", ondelete="CASCADE"),
        nullable=False,
    )
    value: Mapped[Optional[Any]] = mapped_column(sa.JSON, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )

    page_block: Mapped["Block"] = relationship(
        "Block",
        back_populates="property_values",
        lazy="select",
    )
    schema: Mapped["PropertySchema"] = relationship(
        "PropertySchema",
        back_populates="values",
        lazy="select",
    )


class BlockPreference(Base):
    """
    Stores per-block UI preferences as a key/value store.

    The ``(block_id, key)`` unique constraint ensures each block holds at
    most one value per preference key. Upgrade path to per-user preferences:
    add ``user_id`` column and update the unique constraint.

    Common keys:
    - ``folded``: bool – whether the block is collapsed in the nav tree.
    """

    __tablename__ = "block_preferences"
    __table_args__ = (
        sa.UniqueConstraint(
            "block_id", "key", name="uq_block_preferences_block_key"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, primary_key=True, default=uuid.uuid4
    )
    block_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("blocks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    value: Mapped[Optional[Any]] = mapped_column(sa.JSON, nullable=True, default=None)

    block: Mapped["Block"] = relationship(
        "Block",
        back_populates="preferences",
        lazy="select",
    )


class BlockEvent(Base):
    """
    Immutable audit log entry for a single mutation on a block.

    ``block_id`` is nullable with ``SET NULL`` on delete so that event
    records are retained after a block is hard-deleted (purged), preserving
    the full audit trail.

    ``before`` and ``after`` are full snapshots of the mutated fields only –
    not the entire block – keeping records compact.

    Common ``event_type`` values:
    - ``created``
    - ``content_updated``
    - ``renamed``        (block title / name within content)
    - ``icon_changed``
    - ``cover_changed``
    - ``moved``
    - ``state_changed``  (active → trash, trash → active)
    """

    __tablename__ = "block_events"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, primary_key=True, default=uuid.uuid4
    )
    block_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("blocks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    before: Mapped[Optional[Any]] = mapped_column(sa.JSON, nullable=True, default=None)
    after: Mapped[Optional[Any]] = mapped_column(sa.JSON, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )

    block: Mapped[Optional["Block"]] = relationship(
        "Block",
        back_populates="events",
        lazy="select",
    )
