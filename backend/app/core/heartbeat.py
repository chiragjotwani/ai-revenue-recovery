"""Liveness heartbeat for long-running background workers (Phase 14).

The event relay and consumer (``scripts/event_relay.py``,
``scripts/event_consumer.py``) are plain asyncio loops with no HTTP
server -- Docker's ``HEALTHCHECK`` needs a way to ask "is this process
actually alive" without one. ``run_heartbeat_loop`` writes the current
Unix timestamp to a file every ``interval_seconds`` for as long as the
process's event loop is running; ``docker-compose.yml``'s healthcheck
for each worker shells out to ``check_heartbeat`` to test freshness.

Deliberately liveness only, not readiness: a fresh heartbeat proves the
process's event loop is scheduling tasks (not deadlocked/crashed), the
same thing ``GET /health`` proves for the backend -- it says nothing
about whether Kafka is currently reachable, which is exactly the
distinction Phase 14 asks liveness vs. readiness to preserve.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

DEFAULT_HEARTBEAT_PATH = Path("/tmp/arr_worker_heartbeat")
DEFAULT_INTERVAL_SECONDS = 10.0
#: A healthcheck considers the heartbeat stale (process hung/dead) past
#: this many missed intervals -- generous enough to tolerate one slow
#: tick without flapping the container to "unhealthy".
STALE_AFTER_SECONDS = DEFAULT_INTERVAL_SECONDS * 3


def write_heartbeat(path: Path = DEFAULT_HEARTBEAT_PATH) -> None:
    path.write_text(str(time.time()))


async def run_heartbeat_loop(
    path: Path = DEFAULT_HEARTBEAT_PATH, interval_seconds: float = DEFAULT_INTERVAL_SECONDS
) -> None:
    while True:
        write_heartbeat(path)
        await asyncio.sleep(interval_seconds)


def check_heartbeat(path: Path = DEFAULT_HEARTBEAT_PATH) -> bool:
    """Returns ``True`` iff the heartbeat file exists and was written
    within ``STALE_AFTER_SECONDS``. Used as the Docker healthcheck
    command's exit-code source (see ``_main`` below).
    """
    try:
        written_at = float(path.read_text())
    except (FileNotFoundError, ValueError):
        return False
    return (time.time() - written_at) < STALE_AFTER_SECONDS


def _main() -> None:
    sys.exit(0 if check_heartbeat() else 1)


if __name__ == "__main__":
    _main()
