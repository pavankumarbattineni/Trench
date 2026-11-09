import asyncio
import sys
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from app.database.checkpointer import close_checkpointer, init_checkpointer
from app.main import app


@pytest.fixture(scope="session")
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    # psycopg's async pool (the LangGraph checkpointer, opened below for
    # every test) can't run on Windows' default ProactorEventLoop -- every
    # test errored at setup with PoolTimeout. Use the selector loop there,
    # as uvicorn does; other platforms keep their default.
    # Deliberately this (deprecated) fixture rather than the newer
    # pytest_asyncio_loop_factories hook: that hook only applies to async
    # test items, so a sync test running first still opened the
    # session-scoped checkpointer fixture on the Proactor loop and failed.
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


@pytest.fixture(autouse=True, scope="session")
async def _checkpointer_open() -> AsyncGenerator[None]:
    # The LangGraph checkpointer's own connection pool must be opened
    # before any chat test can run the graph, and lifespan never fires
    # under httpx's ASGITransport (it doesn't drive the ASGI lifespan
    # protocol), so it's opened explicitly here instead.
    await init_checkpointer()
    yield
    await close_checkpointer()


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
