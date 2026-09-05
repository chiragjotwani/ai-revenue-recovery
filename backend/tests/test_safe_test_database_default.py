"""Regression test for KI-010 (resolved): a bare ``pytest`` invocation with
no ``DATABASE_URL`` set must resolve to a dedicated test database, never
the shared dev database (``app.core.config.Settings.database_url``'s own
default, ``arr_db``) that ``tests/conftest.py``'s per-test truncation
fixture would otherwise silently wipe.

Runs `conftest.py`'s `os.environ.setdefault(...)` fix in a genuinely clean
subprocess (this test's own process already has `DATABASE_URL` set by
conftest's own import-time side effect, so this cannot be checked
in-process) with ``DATABASE_URL`` explicitly unset, mirroring exactly the
failure mode KI-010 originally described: a contributor running `pytest`
without first exporting anything.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_PRINT_RESOLVED_DATABASE_URL = (
    "import conftest\n"
    "from app.core.config import get_settings\n"
    "print(get_settings().database_url)"
)


def test_bare_pytest_invocation_never_resolves_to_the_dev_database() -> None:
    env = dict(os.environ)
    env.pop("DATABASE_URL", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _PRINT_RESOLVED_DATABASE_URL,
        ],
        cwd=str(_BACKEND_ROOT / "tests"),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    resolved_url = result.stdout.strip()
    assert "arr_test_db" in resolved_url, resolved_url
    # The actual safety invariant: never the literal dev database name.
    assert not resolved_url.rstrip("/").endswith("/arr_db"), resolved_url


def test_explicit_database_url_is_never_overridden() -> None:
    """setdefault must not clobber an operator's own explicit override
    (CI, a differently-named local test DB, etc.) -- only fill the gap
    when nothing was set at all.
    """
    env = dict(os.environ)
    env["DATABASE_URL"] = "postgresql+psycopg://x:y@example-ci-host:5432/some_other_test_db"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _PRINT_RESOLVED_DATABASE_URL,
        ],
        cwd=str(_BACKEND_ROOT / "tests"),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == env["DATABASE_URL"]
