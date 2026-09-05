import os
from collections.abc import AsyncGenerator

# KI-010 fix: a dedicated test database is the automatic default, not
# something every session must remember to opt into. `setdefault` never
# overrides an operator's own explicit `DATABASE_URL` (CI, a differently
# named local test DB, etc.) -- it only fills the gap that previously let
# a bare `pytest` run silently truncate the shared dev database
# (`app.core.config.Settings.database_url`'s own default). This MUST run
# before `app.db.session` is imported (directly or via `app.main` below),
# since that module reads `get_settings().database_url` once at import
# time to build the module-level `engine`.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://arr_user:arr_password@localhost:5433/arr_test_db"
)

# Phase 15 (Security & Fintech Hardening): every mutating endpoint now
# requires an API key (app.core.auth). Tests exercise real request/response
# behavior through the ASGI app rather than mocking auth away, so a fixed
# test operator key is configured the same way a real deployment would --
# see the `client` fixture below, which attaches it by default. Individual
# tests that specifically exercise auth/authz (missing key, readonly key,
# wrong key) override the header explicitly rather than relying on this
# default.
os.environ.setdefault("API_KEYS_RAW", "test-operator-key:operator,test-readonly-key:readonly")

# The full suite fires far more than 120 requests/minute against one test
# API key -- a real production budget, not a bug. Raised here rather than
# disabled outright, so the rate limiter's own dedicated tests
# (test_rate_limiting.py) can still exercise a real, low limit by
# overriding this env var themselves before importing app.main.
os.environ.setdefault("RATE_LIMIT_REQUESTS_PER_MINUTE", "100000")

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base
from app.db.session import AsyncSessionLocal, engine, get_db_session

# Importing app.main applies the Windows event-loop policy fix as a side
# effect, which must happen before any async DB code runs in tests.
from app.main import app

TEST_OPERATOR_API_KEY = "test-operator-key"
TEST_READONLY_API_KEY = "test-readonly-key"


@pytest_asyncio.fixture(autouse=True)
async def _clean_database() -> AsyncGenerator[None, None]:
    """Truncate every table before each test.

    Tests run against a real Postgres instance (per project policy: no
    mocking the database), isolated by resetting state between tests
    rather than mocking away the persistence layer.

    ``engine`` is disposed first: it is a module-level singleton
    constructed once at import time and shared by every test, but
    pytest-asyncio gives each test function its own event loop by
    default. SQLAlchemy's AsyncEngine/AsyncAdaptedQueuePool documents
    this combination as unsafe -- an engine (and the asyncio
    synchronization primitives its pool lazily creates, bound to
    whichever loop was running at the time) must not be reused across
    event loops without an explicit ``dispose()`` in between (see
    docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html), or a stale
    primitive from an earlier, now-closed loop can surface as
    ``RuntimeError: ... is bound to a different event loop`` in a later
    test (observed here under full-suite runs, specifically for
    concurrency tests that stress many simultaneous pool checkouts; see
    the Phase 5C engine-lifecycle and 5D-5F verification reports).
    Disposing here, at the start of every test's own event loop, is the
    SQLAlchemy-documented fix: the pool lazily and cheaply reconstructs
    its primitives against whichever loop is current on first use. This
    is a test-only fix -- the real application runs one persistent event
    loop for its entire process lifetime under uvicorn, so this
    condition cannot occur in production.
    """
    await engine.dispose()
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())
    yield


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    async def _override_get_db_session() -> AsyncGenerator[AsyncSession, None]:
        async with AsyncSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_get_db_session
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-API-Key": TEST_OPERATOR_API_KEY},
    ) as ac:
        yield ac
    app.dependency_overrides.clear()
