"""
Login verification backed by the users database table.

This module is a thin coordination layer: it opens its own session and
delegates credential verification to the user repository. Callers receive
a full User object on success so they can extract the user_id for
session-token creation without a second DB round-trip.
"""
from app.database.database import SessionLocal
from app.users import repository as user_repo
from app.users.model import User


def verifyLogin(username: str, password: str) -> User | None:
    """
    Verify *username* and *password* against the users table.

    Parameters
    ----------
    username:
        The plaintext username to look up.
    password:
        The plaintext password to verify.

    Returns
    -------
    User | None
        The matching active User if credentials are valid, otherwise None.
    """
    with SessionLocal() as db:
        return user_repo.verify_login(db, username, password)
