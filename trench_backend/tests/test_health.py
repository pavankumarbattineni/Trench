import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_check_returns_ok_and_db_connected(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)

    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "connected", "version": "dev"}


@pytest.mark.asyncio
async def test_health_check_reports_the_deployed_commit(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    """The deploy pipeline waits for /health to report the commit it just
    deployed -- Render exposes that commit as RENDER_GIT_COMMIT."""
    monkeypatch.setenv("RENDER_GIT_COMMIT", "0123abcd" * 5)

    response = await client.get("/health")

    assert response.json()["version"] == "0123abcd" * 5
