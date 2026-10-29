"""Tests for the encryption of secret setting values."""
import pytest

from app.settings.secret_box import open_sealed, seal


def test_a_sealed_value_opens_to_the_original():
    assert open_sealed(seal("value-a", "secret-x"), "secret-x") == "value-a"


def test_the_sealed_token_does_not_contain_the_value():
    assert "value-a" not in seal("value-a", "secret-x")


def test_sealing_twice_gives_different_tokens():
    """Fernet adds a random IV, so equal values cannot be recognized as equal."""
    assert seal("value-a", "secret-x") != seal("value-a", "secret-x")


def test_a_token_from_another_secret_key_does_not_open():
    """A changed SECRET_KEY turns stored values into 'not set' instead of failing."""
    assert open_sealed(seal("value-a", "secret-x"), "secret-y") is None


@pytest.mark.parametrize("token", [None, "", "not-a-token", "gAAAAA"])
def test_missing_or_malformed_tokens_do_not_open(token):
    assert open_sealed(token, "secret-x") is None


def test_unicode_survives_the_round_trip():
    assert open_sealed(seal("value ä ß €", "secret-x"), "secret-x") == "value ä ß €"


def test_the_installation_secret_key_is_used_by_default(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "secret-x")
    token = seal("value-a")
    assert open_sealed(token, "secret-x") == "value-a"
    monkeypatch.setenv("SECRET_KEY", "secret-y")
    assert open_sealed(token) is None


def test_sealing_without_a_secret_key_refuses(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        seal("value-a")
