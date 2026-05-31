"""
User registration helper used during initial application setup.

Provides a single convenience function that creates the first admin user in
the database. The setup router calls this; the user repository handles the
actual DB write and password hashing.
"""
from app.database.database import SessionLocal
from app.users import repository as user_repo
from app.users.model import User


def create_admin(username: str, password: str) -> User:
    """
    Create the initial admin user in the database and return it.

    Should only be called when no users exist yet (enforced by the setup
    router's ``_is_configured`` check). Commits the transaction internally.

    Parameters
    ----------
    username:
        The desired username for the admin account.
    password:
        Plaintext password – bcrypt-hashed before storage.

    Returns
    -------
    User
        The newly created and committed User with ``role='admin'``.
    """
    with SessionLocal() as db:
        user = user_repo.create_user(db, username, password, role="admin")
        db.commit()
        db.refresh(user)
    return user
