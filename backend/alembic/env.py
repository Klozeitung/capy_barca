import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

# Ensure the backend root is on sys.path so app.* imports resolve correctly
# regardless of whether Alembic is invoked from the repo root or /app in Docker.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Load .env for local development. In Docker, DATABASE_URL is injected via
# docker-compose env_file / environment, so load_dotenv is a no-op there.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from app.database.database import Base  # noqa: E402

# Register all models with Base so autogenerate can detect schema changes.
import app.automations.automations_models  # noqa: F401, E402 – Automation
import app.blocks.models  # noqa: F401, E402 – Block, PropertySchema, PropertyValue,
                           #                    BlockPreference, BlockEvent
import app.comments.comments_models  # noqa: F401, E402 – Comment
import app.session.session  # noqa: F401, E402

config = context.config

# Override the placeholder sqlalchemy.url from alembic.ini with the real value
# from the environment. This is the single source of truth for the DB URL.
database_url = os.getenv("DATABASE_URL", "sqlite:///./capybarca.db")
config.set_main_option("sqlalchemy.url", database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    Run migrations without an active DB connection.

    Useful for generating SQL scripts to review or apply manually.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
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
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
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
