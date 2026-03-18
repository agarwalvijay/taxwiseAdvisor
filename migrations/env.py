import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Ensure the project root is on sys.path so backend.* imports resolve
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.config import settings
from backend.database import Base

# Import all models so Alembic can detect them for autogenerate
import backend.models.document  # noqa: F401
import backend.models.snapshot  # noqa: F401
import backend.models.plan      # noqa: F401
import backend.models.report    # noqa: F401

config = context.config

# Override sqlalchemy.url from our settings (supports env var injection)
# Convert asyncpg URL to sync psycopg2 URL for Alembic migrations
sync_url = settings.database_url.replace(
    "postgresql+asyncpg://", "postgresql+psycopg2://"
)
config.set_main_option("sqlalchemy.url", sync_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
