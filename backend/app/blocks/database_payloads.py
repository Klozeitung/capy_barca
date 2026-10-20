"""
Request and response models of the database API.

Shared by ``app.blocks.database_router`` (databases, settings, property
schemas) and ``app.blocks.database_entries_router`` (entries, values, entry
templates). The models carry no behaviour; validation beyond field types
happens in the handlers and services.
"""
import uuid
from typing import Any, Optional

from pydantic import BaseModel, Field


class SchemaCreate(BaseModel):
    """
    ``visible_in_view_id`` names the view the property is added from. The new
    property starts hidden in every other view of the database.
    """
    name: str
    type: str
    position: Optional[float] = None
    config: Optional[dict] = None
    group: str = "Standard"
    visible_in_view_id: Optional[uuid.UUID] = None


class SchemaUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    config: Optional[dict] = None
    position: Optional[float] = None
    group: Optional[str] = None


class SchemaResponse(BaseModel):
    id: uuid.UUID
    database_id: uuid.UUID
    name: str
    type: str
    config: Optional[dict]
    position: float
    group: str = "Standard"

    model_config = {"from_attributes": True}


class DatabaseListResponse(BaseModel):
    """Lightweight descriptor of a database block for relation pickers."""

    id: uuid.UUID
    title: Optional[str]

    model_config = {"from_attributes": True}


class EntryResponse(BaseModel):
    """
    A database entry (page-type block) enriched with all its property values.

    ``values`` maps schema_id (as string) to the JSONB value dict.  A missing
    key means the entry has never had a value written for that schema; ``null``
    means the value was explicitly cleared.
    """

    id: uuid.UUID
    position: float
    content: Optional[dict]
    icon: Optional[str]
    state: str
    values: dict[str, Optional[dict]]


class ValueUpsert(BaseModel):
    value: Optional[dict] = None


class ValueResponse(BaseModel):
    """
    The value of one cell as stored after a write.

    Serialised by ``views.entry_payloads``, the same function the entry, batch
    and view endpoints read values with, so it has the shape a later read
    returns: derived parts such as the ``_timeline`` of a timeline relation are
    included, and client input the backend dropped is not. ``None`` when the
    cell holds no value.
    """
    value: Optional[dict] = None


class FormulaValidateRequest(BaseModel):
    expression: str


class FormulaValidateResponse(BaseModel):
    valid: bool
    error: Optional[str] = None
    prop_names: list[str] = []


class QueryFilter(BaseModel):
    """A single filter condition as sent by the frontend."""
    schema_id: str
    operator: str
    value: str = ''
    date_mode: Optional[str] = None
    date_offset: Optional[int] = None
    formula_result_type: Optional[str] = None
    value2: str = ''


class QuerySort(BaseModel):
    """A single sort column as sent by the frontend."""
    schema_id: str
    direction: str = 'asc'


class QueryFilterGroup(BaseModel):
    """A group of filter conditions with a shared conjunction."""
    conjunction: str = 'and'  # 'and' | 'or'
    filters: list[QueryFilter] = []


class EntryQueryRequest(BaseModel):
    """
    POST body for the /entries/query endpoint.

    filter_groups – ordered list of filter groups.  Within each group,
                    conditions are combined by the group's conjunction
                    ('and'|'or').  Groups themselves are ANDed together.
    filters       – legacy flat filter list (treated as a single AND group).
                    Ignored when filter_groups is non-empty.
    sorts         – ordered sort columns; position is the implicit tiebreaker.
    limit         – maximum rows to return; capped server-side at 10 000.
    offset        – zero-based row offset for pagination.
    """
    filter_groups: list[QueryFilterGroup] = []
    filters: list[QueryFilter] = []  # backward compat
    sorts: list[QuerySort] = []
    limit: int = 1000
    offset: int = 0


class EntryQueryResponse(BaseModel):
    """Paginated filtered/sorted entry list."""
    entries: list[EntryResponse]
    total: int


class EntryTitleRequest(BaseModel):
    """
    POST body for the /entries/resolve-titles endpoint.

    ids             – entry IDs to resolve to lightweight descriptors.  IDs
                      that do not correspond to an active, non-template entry
                      of the target database are silently omitted from the
                      response.
    value_schema_id – optional property of the target database whose raw value
                      is returned alongside each descriptor.  Used by keyed
                      relations, which need the key value of every linked
                      entry to sort and render it.  A schema that does not
                      belong to the target database yields ``None`` values
                      rather than an error, so a dangling key pointer degrades
                      to seniority order instead of breaking the cell.
    """
    ids: list[uuid.UUID] = []
    value_schema_id: Optional[uuid.UUID] = None


class EntryTitleResponse(BaseModel):
    """
    Lightweight relation-target descriptor used to render relation chips.

    Carries only what a chip needs (``id``, ``title``, ``database_id``); the
    title is read from ``Block.content['title']`` and may be ``None`` for an
    untitled entry.

    ``value`` holds the raw property value requested via ``value_schema_id``
    and is ``None`` whenever none was requested, the property does not resolve,
    or the entry has no value stored for it.
    """
    id: uuid.UUID
    title: Optional[str]
    database_id: uuid.UUID
    value: Optional[dict] = None


class EntryDetailResponse(BaseModel):
    """
    One entry with its values and the resolution of the relations it holds,
    in the same shape a view page uses for its ``relations`` and
    ``key_values``.
    """
    entry: EntryResponse
    relations: dict[str, dict]
    key_values: dict[str, dict[str, Any]]


# Entries one batch request may ask for.
ENTRY_BATCH_LIMIT = 500


class EntryBatchRequest(BaseModel):
    """Entries to load, by id."""
    ids: list[uuid.UUID] = Field(max_length=ENTRY_BATCH_LIMIT)


class EntryBatchResponse(BaseModel):
    """
    Several entries with their values and the resolution of their relations.

    ``missing`` lists the requested ids that are not active entries of the
    database (trashed, deleted, templates or unknown).
    """
    entries: list[EntryResponse]
    missing: list[uuid.UUID]
    relations: dict[str, dict]
    key_values: dict[str, dict[str, Any]]


class OptionUsageResponse(BaseModel):
    """How many active entries use one option of a property."""
    entries: int


class KeyReferenceResponse(BaseModel):
    """
    One relation property that keys on a given property of another database.

    Returned by the key-references pre-flight so a delete confirmation can name
    the relations it is about to reset, rather than merely reporting that some
    exist. ``schema_name`` and ``database_title`` are ``None`` for a relation
    in a database the caller may not read.
    """
    schema_id: uuid.UUID
    schema_name: Optional[str]
    database_id: uuid.UUID
    database_title: Optional[str]


# An icon as the frontend's icon picker names it: one of the bundled Iconify
# sets, a colon, and the icon's name within the set. The same rule applies to
# the recently used icons of a user (``app.users.router``).
_ICON_PATTERN = r"^(mdi|tabler|lucide):[a-z0-9]+(-[a-z0-9]+)*$"


class DatabaseSettingsResponse(BaseModel):
    """
    Settings of a whole database.

    ``can_manage`` tells the caller whether they may change them: the
    database's owner and admins may.
    """
    default_entry_icon: Optional[str]
    can_manage: bool


class DatabaseSettingsUpdate(BaseModel):
    """New settings; ``default_entry_icon`` ``None`` removes the icon."""
    default_entry_icon: Optional[str] = Field(default=None, max_length=128, pattern=_ICON_PATTERN)


class EntryTemplateResponse(BaseModel):
    """A database entry template block with its property values."""
    id: uuid.UUID
    position: float
    content: Optional[dict]
    icon: Optional[str]
    values: dict[str, Optional[dict]]
