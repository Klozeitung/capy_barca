"""
Block type registry.

Defines the complete set of valid block type strings and provides a
validation helper used at both the API boundary (Pydantic field_validator)
and in the service layer (defense-in-depth).

Adding a new block type requires a single change here; every validation
point picks it up automatically.
"""
from typing import Final

# Complete set of valid block type strings. A frozenset gives O(1) membership
# checks and makes the intent (immutable registry) explicit.
BLOCK_TYPES: Final[frozenset[str]] = frozenset(
    {
        # ── Structural ──────────────────────────────────────────────────────
        "workspace",
        "page",
        "database",
        "database_view",
        "linked_database",  # inline view of an existing database; reference_id → target DB
        # ── Text ────────────────────────────────────────────────────────────
        "paragraph",
        "text_toggle",           # collapsible paragraph; children stored in DB like toggle
        "heading_1",
        "heading_2",
        "heading_3",
        "heading_4",
        "heading_1_toggle",   # collapsible heading 1; toggle state in content.toggled
        "heading_2_toggle",   # collapsible heading 2; toggle state in content.toggled
        "heading_3_toggle",   # collapsible heading 3; toggle state in content.toggled
        "heading_4_toggle",   # collapsible heading 4; toggle state in content.toggled
        "bulleted_list_item",
        "numbered_list_item",
        "to_do",
        "toggle",
        "code",
        "quote",
        "callout",
        "divider",
        # ── Media (Tier 2a) ─────────────────────────────────────────────────
        "image",
        "video",
        "audio",
        "pdf",
        # ── File & Drive (Tier 2b) ───────────────────────────────────────────
        "file",
        "drive",
        # ── Meta (Tier 2c) ───────────────────────────────────────────────────
        "bookmark",
        "embed",
        "table_of_contents",  # auto-generated heading outline; no stored content
        # ── Layout (Tier 3) ──────────────────────────────────────────────────
        "layout",          # container: holds column children, stores widths in content
        "column",          # child of layout: holds any block as children
        "synched_origin",  # synced container: children are shared with all its mirrors
        "synched_mirror",  # pointer to a synched_origin; renders origin's children
                           # reference_id → synched_origin block
                           # content.locked (bool) → if true, mirror is read-only
    }
)


def validate_block_type(block_type: str) -> str:
    """
    Assert that *block_type* is a registered block type and return it.

    Parameters
    ----------
    block_type:
        The type string to validate.

    Returns
    -------
    str
        The validated type string, unchanged.

    Raises
    ------
    ValueError
        If *block_type* is not a member of :data:`BLOCK_TYPES`.
    """
    if block_type not in BLOCK_TYPES:
        allowed = ", ".join(sorted(BLOCK_TYPES))
        raise ValueError(
            f"Unknown block type '{block_type}'. Allowed types: {allowed}"
        )
    return block_type
