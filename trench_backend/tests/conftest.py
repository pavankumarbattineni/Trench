from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from app.database.checkpointer import close_checkpointer, init_checkpointer
from app.main import app


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
