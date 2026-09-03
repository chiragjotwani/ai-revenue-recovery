"""Phase 14: worker liveness heartbeat (``app.core.heartbeat``).

Pure filesystem/time logic -- no database needed, unlike every other
test file in this suite.
"""

from __future__ import annotations

import time

from app.core.heartbeat import STALE_AFTER_SECONDS, check_heartbeat, write_heartbeat


def test_check_heartbeat_false_when_file_missing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    missing = tmp_path / "does-not-exist"
    assert check_heartbeat(missing) is False


def test_check_heartbeat_true_immediately_after_write(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "heartbeat"
    write_heartbeat(path)
    assert check_heartbeat(path) is True


def test_check_heartbeat_false_when_stale(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "heartbeat"
    path.write_text(str(time.time() - STALE_AFTER_SECONDS - 1))
    assert check_heartbeat(path) is False


def test_check_heartbeat_false_on_garbage_content(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "heartbeat"
    path.write_text("not-a-timestamp")
    assert check_heartbeat(path) is False
