"""
User registration helper used during initial application setup.

Provides a single function that creates the first admin user in the
database. The setup router calls this; the user repository handles the
actual DB write and password hashing.

Concurrency
-----------
Before the first account exists, whoever reaches the setup page first becomes
the administrator. Checking for existing users and then inserting one are two
steps, so two requests arriving together could both pass the check and both
create an administrator. ``create_admin`` therefore takes a PostgreSQL
advisory lock inside its transaction and checks again while holding it: a
second request waits, sees the first account once the first transaction has
committed, and is refused.
"""
import sqlalchemy as sa

from app.database.database import SessionLocal
from app.users import repository as user_repo
from app.users.model import User

# Key of the advisory lock that serialises initial registrations. Any fixed
# 64-bit value works as long as nothing else in the database uses it; this one
# is the ASCII text "CapyReg1". The migration lock uses a different key.
REGISTRATION_LOCK_KEY = 0x4361707952656731

_LOCK_STATEMENT = sa.text("SELECT pg_advisory_xact_lock(:key)")


class AlreadyConfigured(Exception):
    """Raised when an account already exists, so no initial admin may be created."""


def create_admin(username: str, password: str) -> User:
    """
    Create the initial admin user in the database and return it.

    Refuses once any user exists. The check runs under a transaction-scoped
    advisory lock, so concurrent calls are serialised and at most one of them
    creates an account. The lock is released on commit or rollback, also when
    the process dies.

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

    Raises
    ------
    AlreadyConfigured
        If at least one user exists when the lock is held.
    """
    with SessionLocal() as db:
        if db.bind.dialect.name == "postgresql":
            db.execute(_LOCK_STATEMENT, {"key": REGISTRATION_LOCK_KEY})
        if db.query(User).count() > 0:
            db.rollback()
            raise AlreadyConfigured("An account already exists")
        user = user_repo.create_user(db, username, password, role="admin")
        db.commit()
        db.refresh(user)
    return user
