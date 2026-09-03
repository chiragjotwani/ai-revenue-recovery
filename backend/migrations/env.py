from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.db.base import Base
from app.models import (  # noqa: F401  (registers models)
    Customer,
    DecisionResult,
    Diagnosis,
    IngestionEvent,
    Payment,
    RecoveryCase,
    RecoveryCaseTransition,
)

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False (Phase 14): the default (True) would
    # silently disable every application logger not explicitly listed in
    # alembic.ini's [loggers] section (only root/sqlalchemy/alembic are)
    # -- including every app.* logger app.core.logging configures. Harmless
    # in the production container (alembic runs as its own short-lived
    # process, separate from the uvicorn process), but a real defect
    # in-process: tests/test_migrations.py runs alembic commands directly
    # inside the same pytest process as every other test, which was
    # permanently silencing app.http's (and every other app logger's) log
    # records for the rest of that test run once discovered.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

config.set_main_option("sqlalchemy.url", get_settings().database_url)


def run_migrations_offline() -> None:
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
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
