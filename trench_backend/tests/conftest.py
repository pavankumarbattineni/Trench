from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from app.jobs.app import app as procrastinate_app
from app.main import app


@pytest.fixture(autouse=True, scope="session")
async def _procrastinate_app_open() -> AsyncGenerator[None]:
    # Any test that calls DocumentService.upload_document() (directly, or
    # via the `client` fixture -- httpx's ASGITransport doesn't drive the
    # ASGI lifespan protocol, so app's `lifespan=` never runs under it)
    # needs the procrastinate connector open for `defer_async()` to work.
    async with procrastinate_app.open_async():
        yield


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
