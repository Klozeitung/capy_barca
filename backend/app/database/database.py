import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# URL schemes that address PostgreSQL through a driver other than the one this
# application installs. Each is rewritten to the psycopg 3 dialect:
#
#   postgresql://           No driver named. SQLAlchemy 2.0 resolved this to
#                           psycopg2, 2.1 resolves it to psycopg. Stating the
#                           driver keeps the result independent of that default.
#   postgres://             Legacy alias that SQLAlchemy no longer accepts, but
#                           which hosting tools and older .env files still emit.
#   postgresql+psycopg2://  Written by earlier installations, or by hand as a
#                           workaround. psycopg2 is no longer installed.
#
# Only the scheme is touched. The remainder of the URL, including the
# percent-encoded credentials setup.sh writes, is passed through byte for byte.
_POSTGRES_DRIVER_SCHEME = "postgresql+psycopg"
_REWRITTEN_SCHEMES = frozenset({"postgresql", "postgres", "postgresql+psycopg2"})


def normalize_database_url(url: str) -> str:
    """
    Return ``url`` with any PostgreSQL scheme pinned to the psycopg 3 driver.

    URLs for other backends, and PostgreSQL URLs that already name a different
    driver explicitly (asyncpg, pg8000, psycopg), are returned unchanged.
    """
    scheme, separator, remainder = url.partition("://")
    if not separator:
        return url
    if scheme.lower() in _REWRITTEN_SCHEMES:
        return f"{_POSTGRES_DRIVER_SCHEME}://{remainder}"
    return url


def _require_database_url() -> str:
    """
    Return the configured database URL, refusing to start without one.

    PostgreSQL is the only supported backend: the models use JSONB, which no
    other database provides. There is deliberately no fallback, so a missing
    variable fails loudly at start-up instead of producing an application that
    breaks on the first JSON query.
    """
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. CapyBarca requires a PostgreSQL database; "
            "docker-compose.yml provides the variable, and the test suite derives "
            "it from TEST_DATABASE_URL."
        )
    return normalize_database_url(url)


def _int_from_env(name: str, default: int, minimum: int) -> int:
    """Read a non-negative integer setting, falling back to *default* when unset."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from None
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}, got {value}")
    return value


# ─── Connection pool ──────────────────────────────────────────────────────────
#
# Every request holds one pooled connection for its whole duration (see
# app.session.deps.get_db), and app.session.deps admits only as many requests
# at a time as the pool can serve. The pool is sized for AnyIO's 40 worker
# threads:
# 10 connections kept open plus up to 30 opened under load. With fewer, a burst
# of requests queues on the pool instead of on the database.
#
# pool_pre_ping tests a connection before handing it out, so a connection the
# server closed in the meantime (database restart, idle timeout) is replaced
# transparently instead of failing the request that drew it.

_POOL_SIZE = _int_from_env("DB_POOL_SIZE", default=10, minimum=1)
_MAX_OVERFLOW = _int_from_env("DB_MAX_OVERFLOW", default=30, minimum=0)

# The most connections the pool hands out at the same time.
POOL_CAPACITY = _POOL_SIZE + _MAX_OVERFLOW

_DATABASE_URL: str = _require_database_url()

engine = create_engine(
    _DATABASE_URL,
    pool_size=_POOL_SIZE,
    max_overflow=_MAX_OVERFLOW,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass
