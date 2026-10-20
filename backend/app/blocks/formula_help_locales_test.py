"""
The formula syntax reference names its entries and categories by key; the
frontend shows their text from its locales. These tests keep the two in step:
every entry and category the engine lists has its text in every locale, and
the locales describe nothing the engine no longer lists.

They read the locale files of the frontend next to the backend and are
skipped where the backend runs without it (the backend Docker image).
"""
import json
from pathlib import Path

import pytest

from app.blocks.formula_engine import SYNTAX_CATEGORIES, syntax_catalogue

_LOCALES = Path(__file__).resolve().parents[3] / "frontend" / "src" / "locales"

pytestmark = pytest.mark.skipif(
    not _LOCALES.is_dir(), reason="frontend locales not available",
)


def _locales() -> dict[str, dict]:
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(_LOCALES.glob("*.json"))
    }


def test_there_are_locales_to_check():
    assert {"en", "de"} <= set(_locales())


@pytest.mark.parametrize("language", ["en", "de"])
def test_every_entry_has_a_description_and_an_example(language):
    help_ = _locales()[language]["db"]["formulaHelp"]
    for entry in syntax_catalogue():
        text = help_.get(entry.key)
        assert text, f"{language}: no help for '{entry.key}'"
        assert text["description"].strip(), f"{language}: '{entry.key}' has no description"
        assert text["example"].strip(), f"{language}: '{entry.key}' has no example"


@pytest.mark.parametrize("language", ["en", "de"])
def test_locales_describe_no_entry_the_engine_does_not_list(language):
    help_ = _locales()[language]["db"]["formulaHelp"]
    assert set(help_) == {entry.key for entry in syntax_catalogue()}


@pytest.mark.parametrize("language", ["en", "de"])
def test_every_category_has_a_label(language):
    settings = _locales()[language]["db"]["settings"]
    for category in SYNTAX_CATEGORIES:
        assert settings.get(f"formulaHelpCat_{category}", "").strip(), (
            f"{language}: no label for category '{category}'"
        )
