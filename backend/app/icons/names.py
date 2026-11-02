"""
Icon names.

An icon is stored as a string in one of two forms:

* ``<set>:<name>``, an icon of one of the Iconify sets the frontend bundles
  (``mdi``, ``tabler``, ``lucide``), e.g. ``mdi:file-document``.
* ``custom:<id>``, an image an admin uploaded (``app.icons.model``), with the
  icon's UUID in canonical lowercase form.

The frontend registers the uploaded icons as an Iconify set of their own named
``custom``, so both forms render through the same component there.

``ICON_PATTERN`` is the one rule for every request field that takes an icon
name and is validated, so the bundled sets and the custom form cannot drift
apart between endpoints.
"""
import uuid
from typing import Optional

CUSTOM_PREFIX = "custom:"

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

ICON_PATTERN = (
    r"^(?:(?:mdi|tabler|lucide):[a-z0-9]+(?:-[a-z0-9]+)*"
    rf"|custom:{_UUID})$"
)


def custom_icon_name(icon_id: uuid.UUID) -> str:
    """Return the icon string that references the custom icon *icon_id*."""
    return f"{CUSTOM_PREFIX}{icon_id}"


def custom_icon_id(icon: Optional[str]) -> Optional[uuid.UUID]:
    """
    Return the UUID *icon* references when it names a custom icon, else None.

    Anything that is not exactly ``custom:`` followed by a UUID yields None,
    so the result can be used to look the icon up without further checks.
    """
    if not icon or not icon.startswith(CUSTOM_PREFIX):
        return None
    try:
        return uuid.UUID(icon[len(CUSTOM_PREFIX):])
    except ValueError:
        return None
