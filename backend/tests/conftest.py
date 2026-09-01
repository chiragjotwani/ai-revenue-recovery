from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base
from app.db.session import AsyncSessionLocal, engine, get_db_session

# Importing app.main applies the Windows event-loop policy fix as a side
# effect, which must happen before any async DB code runs in tests.
from app.main import app


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
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
