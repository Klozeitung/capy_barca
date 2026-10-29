"""
Encryption of secret setting values at rest.

Keys and header values entered in the "API" settings are stored in the
database, which ``pg_dump`` copies into every backup. They are therefore kept
encrypted there with Fernet (AES-128-CBC with an HMAC-SHA256 tag) under a key
derived from the installation's ``SECRET_KEY`` by HKDF. ``SECRET_KEY`` lives in
``.env`` (mode 600), so a database dump alone does not reveal them.

A changed ``SECRET_KEY`` makes the stored values unreadable. ``open_sealed``
then returns ``None``, which the settings treat as "not set": the admin enters
the value again, nothing fails.
"""
import base64
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# Binds the derived key to this one purpose, so it differs from any other key
# a future feature might derive from the same SECRET_KEY.
_KDF_INFO = b"capybarca app-settings secret values v1"


def _secret_key() -> str:
    secret = os.environ.get("SECRET_KEY", "")
    if not secret:
        raise RuntimeError("SECRET_KEY is not set")
    return secret


def _fernet(secret: Optional[str] = None) -> Fernet:
    raw = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_KDF_INFO,
    ).derive((secret if secret is not None else _secret_key()).encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(raw))


def seal(plain: str, secret: Optional[str] = None) -> str:
    """Encrypt *plain* and return the token to store."""
    return _fernet(secret).encrypt(plain.encode("utf-8")).decode("ascii")


def open_sealed(token: Optional[str], secret: Optional[str] = None) -> Optional[str]:
    """
    Decrypt a stored *token*.

    Returns ``None`` for a missing token and for one that cannot be decrypted,
    typically because ``SECRET_KEY`` changed since it was stored.
    """
    if not token:
        return None
    try:
        return _fernet(secret).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None
