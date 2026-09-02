"""Rebuild the Phase 13 analytics warehouse.

Usage:
    python scripts/build_analytics_warehouse.py

Recomputes ``case_analytics_facts`` from the current operational tables
(see ``app.warehouse.etl.rebuild_warehouse``) and upserts every row.
Idempotent -- safe to run on a schedule or after any batch of new cases.
"""

from __future__ import annotations

import asyncio
import logging

from app.db.session import AsyncSessionLocal
from app.warehouse.etl import rebuild_warehouse

logger = logging.getLogger("app.warehouse.build")


async def main() -> None:
    async with AsyncSessionLocal() as session:
        result = await rebuild_warehouse(session)
    logger.info("analytics warehouse rebuilt: facts_written=%s", result.facts_written)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
