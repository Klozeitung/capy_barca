"""
User repository.

Pure database access functions for the User model. All functions accept an
explicit ``db: Session`` argument and contain no business logic beyond the
direct DB operation. Callers are responsible for committing the transaction.
"""
import uuid
from datetime import datetime

import bcrypt
from sqlalchemy.orm import Session

from app.users.model import User

_BCRYPT_ROUNDS = 12

# How many recently used icons a user keeps (see ``record_icon_use``).
RECENT_ICONS_LIMIT = 10


# ─── Password helpers ─────────────────────────────────────────────────────────


def hash_password(plain: str) -> str:
    """Return a bcrypt hash of *plain* with the configured work factor."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches *hashed*."""
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


# ─── CRUD ─────────────────────────────────────────────────────────────────────


def create_user(db: Session, username: str, password: str, role: str = "member") -> User:
    """
    Create a new User with a bcrypt-hashed password.

    The caller must commit the session after this call.
    """
    user = User(
        id=uuid.uuid4(),
        username=username,
        password_hash=hash_password(password),
        role=role,
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def get_by_id(db: Session, user_id: uuid.UUID) -> User | None:
    """Return the User with the given *user_id*, or None if not found."""
    return db.get(User, user_id)


def get_by_username(db: Session, username: str) -> User | None:
    """Return the User with the given *username*, or None if not found."""
    return db.query(User).filter(User.username == username).first()


def list_users(db: Session) -> list[User]:
    """Return all users ordered by creation time ascending."""
    return db.query(User).order_by(User.created_at).all()


def verify_login(db: Session, username: str, password: str) -> User | None:
    """
    Verify credentials and return the matching active User, or None.
    """
    user = get_by_username(db, username)
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def update_username(db: Session, user: User, new_username: str) -> None:
    """
    Update the username for *user*.

    The caller must commit the session after this call.
    Raises IntegrityError if *new_username* is already taken.
    """
    user.username = new_username
    db.flush()


def update_role(db: Session, user: User, new_role: str) -> None:
    """
    Update the role for *user*.

    The caller must commit the session after this call.
    """
    user.role = new_role
    db.flush()


def update_date_format(db: Session, user: User, new_format: str) -> None:
    """
    Update the preferred display date format for *user*.

    *new_format* is one of the canonical display tokens (validated by the
    router). Storage and interchange of dates remain ISO 8601 regardless of
    this preference. The caller must commit the session after this call.
    """
    user.date_format = new_format
    db.flush()


def record_icon_use(db: Session, user: User, icon: str, used_at: datetime) -> list[dict]:
    """
    Note that *user* picked *icon* at *used_at* and return the updated list.

    An icon already in the list gets the new timestamp; a new one is added
    and, when the list holds ``RECENT_ICONS_LIMIT`` icons already, replaces
    the one used longest ago. The list is stored and returned newest first.

    The user row is locked for the rest of the transaction before the list is
    read, so two requests at the same time (two open tabs) apply one after
    the other instead of one overwriting the other's entry.

    The caller must commit the session after this call.
    """
    db.refresh(user, with_for_update=True)
    # The stored list is newest first, so the new use goes to the front and
    # the entry used longest ago is the one cut off at the end.
    others = [e for e in (user.recent_icons or []) if e.get("icon") != icon]
    entry = {"icon": icon, "used_at": used_at.isoformat()}
    user.recent_icons = [entry, *others][:RECENT_ICONS_LIMIT]
    db.flush()
    return user.recent_icons


def update_password(db: Session, user: User, new_password: str) -> None:
    """
    Replace the user's password with a new bcrypt hash.

    The caller must commit the session after this call.
    """
    user.password_hash = hash_password(new_password)
    db.flush()


def deactivate(db: Session, user: User) -> None:
    """
    Set *user.is_active* to False.

    The caller must commit the session after this call.
    """
    user.is_active = False
    db.flush()
