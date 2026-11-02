"""Tests for the shared icon name rule (app.icons.names)."""
import re
import uuid

import pytest

from app.icons.names import ICON_PATTERN, custom_icon_id, custom_icon_name

_ICON_ID = uuid.UUID("3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b")


@pytest.mark.parametrize("icon", [
    "mdi:file-document",
    "tabler:star",
    "lucide:arrow-up-right",
    "mdi:numeric-1-box",
    f"custom:{_ICON_ID}",
])
def test_pattern_accepts_bundled_and_custom_icons(icon):
    assert re.fullmatch(ICON_PATTERN, icon)


@pytest.mark.parametrize("icon", [
    "file-document",
    "other:star",
    "mdi:File",
    "mdi:star-",
    "custom:star",
    f"custom:{str(_ICON_ID).upper()}",
    f"custom:{_ICON_ID.hex}",
    f"custom:{_ICON_ID}-x",
    "",
])
def test_pattern_refuses_everything_else(icon):
    assert not re.fullmatch(ICON_PATTERN, icon)


def test_name_and_id_round_trip():
    name = custom_icon_name(_ICON_ID)
    assert name == f"custom:{_ICON_ID}"
    assert custom_icon_id(name) == _ICON_ID


@pytest.mark.parametrize("icon", [None, "", "mdi:star", "custom:", "custom:not-a-uuid"])
def test_id_is_none_for_anything_but_a_custom_icon(icon):
    assert custom_icon_id(icon) is None
