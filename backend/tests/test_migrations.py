"""Migration reversibility regression test (BUG-001).

The initial migration used to create the ``payment_status`` Postgres enum
implicitly (via ``create_table``) but never drop it, so
``upgrade head -> downgrade base -> upgrade head`` failed on the second
upgrade with ``type "payment_status" already exists``.

This test runs that exact roundtrip -- twice -- against a throwaway
database, so the regression cannot come back silently.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.core.config import get_settings

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_MIGRATION_DB = "arr_migration_roundtrip_test"


def _swap_db(url_str: str, database: str) -> str:
    return sa.make_url(url_str).set(database=database).render_as_string(hide_password=False)


@pytest.fixture
def throwaway_db_url() -> Iterator[str]:
    base = get_settings().database_url
    maintenance_url = _swap_db(base, "postgres")
    target_url = _swap_db(base, _MIGRATION_DB)

    def _run(sql: str) -> None:
        engine = sa.create_engine(maintenance_url, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as conn:
                conn.execute(sa.text(sql))
        finally:
            engine.dispose()

    _run(f'DROP DATABASE IF EXISTS "{_MIGRATION_DB}" WITH (FORCE)')
    _run(f'CREATE DATABASE "{_MIGRATION_DB}"')
    try:
        yield target_url
    finally:
        _run(f'DROP DATABASE IF EXISTS "{_MIGRATION_DB}" WITH (FORCE)')


def _alembic_config() -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "migrations"))
    return cfg


def _enum_types(db_url: str) -> set[str]:
    engine = sa.create_engine(db_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT typname FROM pg_type "
                    "WHERE typname IN ('payment_status', 'recovery_case_state')"
                )
            )
            return {r[0] for r in rows}
    finally:
        engine.dispose()


def test_upgrade_downgrade_roundtrip_is_repeatable(
    throwaway_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # migrations/env.py builds the URL from get_settings().database_url;
    # redirect that (and clear the lru_cache) at the env-var level.
    monkeypatch.setenv("DATABASE_URL", throwaway_db_url)
    get_settings.cache_clear()
    try:
        cfg = _alembic_config()

        command.upgrade(cfg, "head")
        assert _enum_types(throwaway_db_url) == {"payment_status", "recovery_case_state"}

        command.downgrade(cfg, "base")
        assert (
            _enum_types(throwaway_db_url) == set()
        ), "downgrade base must drop every enum type the migrations created"

        # The roundtrip that used to raise DuplicateObject on the 2nd upgrade:
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")

        assert _enum_types(throwaway_db_url) == {"payment_status", "recovery_case_state"}
    finally:
        get_settings.cache_clear()
