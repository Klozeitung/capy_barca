"""
Tests for the block type registry.
"""
import pytest

from app.blocks.types import BLOCK_TYPES, validate_block_type


def test_block_types_is_frozenset():
    assert isinstance(BLOCK_TYPES, frozenset)


def test_block_types_contains_expected_types():
    expected = {"page", "database", "paragraph", "workspace"}
    assert expected.issubset(BLOCK_TYPES)


# ── Tier 2a: media types ──────────────────────────────────────────────────────


def test_block_types_contains_image():
    assert "image" in BLOCK_TYPES


def test_block_types_contains_video():
    assert "video" in BLOCK_TYPES


def test_block_types_contains_audio():
    assert "audio" in BLOCK_TYPES


def test_block_types_contains_pdf():
    assert "pdf" in BLOCK_TYPES


# ── Tier 2b: file and drive ───────────────────────────────────────────────────


def test_block_types_contains_file():
    assert "file" in BLOCK_TYPES


def test_block_types_contains_drive():
    assert "drive" in BLOCK_TYPES


# ── Tier 2c: meta types ───────────────────────────────────────────────────────


def test_block_types_contains_bookmark():
    assert "bookmark" in BLOCK_TYPES


def test_block_types_contains_embed():
    assert "embed" in BLOCK_TYPES


def test_block_types_contains_table_of_contents():
    assert "table_of_contents" in BLOCK_TYPES


def test_validate_table_of_contents():
    assert validate_block_type("table_of_contents") == "table_of_contents"


# ── Tier 3: layout types ──────────────────────────────────────────────────────


def test_block_types_contains_layout():
    assert "layout" in BLOCK_TYPES


def test_block_types_contains_column():
    assert "column" in BLOCK_TYPES


def test_validate_tier3_layout_types():
    assert validate_block_type("layout") == "layout"
    assert validate_block_type("column") == "column"


def test_layout_and_column_are_distinct_types():
    assert "layout" != "column"
    assert "layout" in BLOCK_TYPES
    assert "column" in BLOCK_TYPES


# ── text_toggle ───────────────────────────────────────────────────────────────


def test_block_types_contains_text_toggle():
    assert "text_toggle" in BLOCK_TYPES


def test_validate_text_toggle():
    assert validate_block_type("text_toggle") == "text_toggle"


def test_text_toggle_is_distinct_from_paragraph():
    assert "paragraph" in BLOCK_TYPES
    assert "text_toggle" in BLOCK_TYPES
    assert "paragraph" != "text_toggle"


def test_text_toggle_is_distinct_from_toggle():
    assert "toggle" in BLOCK_TYPES
    assert "text_toggle" in BLOCK_TYPES
    assert "toggle" != "text_toggle"


# ── heading_4 ─────────────────────────────────────────────────────────────────


def test_block_types_contains_heading_4():
    assert "heading_4" in BLOCK_TYPES


def test_validate_heading_4():
    assert validate_block_type("heading_4") == "heading_4"


def test_heading_4_is_distinct_from_heading_3():
    assert "heading_3" in BLOCK_TYPES
    assert "heading_4" in BLOCK_TYPES
    assert "heading_3" != "heading_4"


# ── Toggle heading types ──────────────────────────────────────────────────────


def test_block_types_contains_all_toggle_headings():
    for t in ("heading_1_toggle", "heading_2_toggle", "heading_3_toggle", "heading_4_toggle"):
        assert t in BLOCK_TYPES


def test_validate_all_toggle_heading_types():
    for t in ("heading_1_toggle", "heading_2_toggle", "heading_3_toggle", "heading_4_toggle"):
        assert validate_block_type(t) == t


def test_toggle_headings_distinct_from_static_headings():
    pairs = [
        ("heading_1", "heading_1_toggle"),
        ("heading_2", "heading_2_toggle"),
        ("heading_3", "heading_3_toggle"),
        ("heading_4", "heading_4_toggle"),
    ]
    for static, toggle in pairs:
        assert static in BLOCK_TYPES
        assert toggle in BLOCK_TYPES
        assert static != toggle


def test_all_four_toggle_heading_levels_are_distinct():
    toggle_types = ["heading_1_toggle", "heading_2_toggle", "heading_3_toggle", "heading_4_toggle"]
    assert len(set(toggle_types)) == 4


# ── Validation helpers ────────────────────────────────────────────────────────


def test_validate_block_type_returns_type_unchanged():
    assert validate_block_type("page") == "page"
    assert validate_block_type("database") == "database"
    assert validate_block_type("paragraph") == "paragraph"


def test_validate_tier2_media_types():
    for t in ("image", "video", "audio", "pdf"):
        assert validate_block_type(t) == t


def test_validate_tier2_file_types():
    assert validate_block_type("file") == "file"
    assert validate_block_type("drive") == "drive"


def test_validate_tier2_meta_types():
    assert validate_block_type("bookmark") == "bookmark"
    assert validate_block_type("embed") == "embed"


def test_validate_block_type_raises_for_unknown_type():
    with pytest.raises(ValueError, match="Unknown block type"):
        validate_block_type("not_a_real_type")


def test_validate_block_type_error_message_lists_allowed():
    with pytest.raises(ValueError) as exc_info:
        validate_block_type("bogus")
    assert "page" in str(exc_info.value)


def test_all_types_pass_validation():
    for block_type in BLOCK_TYPES:
        assert validate_block_type(block_type) == block_type


# ── linked_database ───────────────────────────────────────────────────────────


def test_block_types_contains_linked_database():
    assert "linked_database" in BLOCK_TYPES


def test_validate_linked_database():
    assert validate_block_type("linked_database") == "linked_database"


def test_linked_database_is_distinct_from_database():
    assert "database" in BLOCK_TYPES
    assert "linked_database" in BLOCK_TYPES
    assert "database" != "linked_database"


def test_linked_database_is_distinct_from_database_view():
    assert "database_view" in BLOCK_TYPES
    assert "linked_database" in BLOCK_TYPES
    assert "database_view" != "linked_database"


# ── synched_origin and synched_mirror (Tier 3 synced blocks) ──────────────────


def test_block_types_contains_synched_origin():
    assert "synched_origin" in BLOCK_TYPES


def test_block_types_contains_synched_mirror():
    assert "synched_mirror" in BLOCK_TYPES


def test_validate_synched_origin():
    assert validate_block_type("synched_origin") == "synched_origin"


def test_validate_synched_mirror():
    assert validate_block_type("synched_mirror") == "synched_mirror"


def test_synched_origin_and_mirror_are_distinct():
    assert "synched_origin" in BLOCK_TYPES
    assert "synched_mirror" in BLOCK_TYPES
    assert "synched_origin" != "synched_mirror"


def test_synched_types_are_distinct_from_layout():
    assert "synched_origin" != "layout"
    assert "synched_mirror" != "layout"
    assert "synched_origin" in BLOCK_TYPES
    assert "synched_mirror" in BLOCK_TYPES


# ── entry_template ────────────────────────────────────────────────────────────


def test_block_types_contains_entry_template():
    assert "entry_template" in BLOCK_TYPES


def test_validate_entry_template():
    assert validate_block_type("entry_template") == "entry_template"


def test_entry_template_is_distinct_from_page():
    assert "page" in BLOCK_TYPES
    assert "entry_template" in BLOCK_TYPES
    assert "page" != "entry_template"


def test_entry_template_is_distinct_from_database():
    assert "database" in BLOCK_TYPES
    assert "entry_template" in BLOCK_TYPES
    assert "database" != "entry_template"
