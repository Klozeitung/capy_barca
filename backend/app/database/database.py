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


_DATABASE_URL: str = normalize_database_url(
    os.getenv("DATABASE_URL", "sqlite:///./capybarca.db")
)

_connect_args = {"check_same_thread": False} if _DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(_DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass
