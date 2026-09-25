import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import create_engine, pool

# Ensure the backend root is on sys.path so app.* imports resolve correctly
# regardless of whether Alembic is invoked from the repo root or /app in Docker.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Load .env for local development. In Docker, DATABASE_URL is injected via
# docker-compose env_file / environment, so load_dotenv is a no-op there.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from app.database.database import Base, engine  # noqa: E402

# Register all models with Base so autogenerate can detect schema changes.
import app.automations.automations_models  # noqa: F401, E402 – Automation
import app.blocks.models  # noqa: F401, E402 – Block, PropertySchema, PropertyValue,
                           #                    BlockPreference, BlockEvent
import app.comments.comments_models  # noqa: F401, E402 – Comment
import app.session.session  # noqa: F401, E402

config = context.config

# The database URL is taken from the application's engine rather than read
# from the environment a second time. That keeps a single source of truth,
# including the driver normalisation in app.database.database, so migrations
# and the running application can never resolve the same DATABASE_URL to
# different drivers.
#
# The URL is deliberately not written into the Alembic config. Config values
# pass through configparser interpolation, which treats '%' as a directive,
# and the credentials setup.sh writes are percent-encoded.
database_url = engine.url

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    Run migrations without an active DB connection.

    Useful for generating SQL scripts to review or apply manually.
    """
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations against a live DB connection.

    NullPool is used intentionally: Alembic's migration process is short-lived
    and should not hold connections open.
    """
    connectable = create_engine(database_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
