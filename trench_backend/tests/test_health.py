import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_check_returns_ok_and_db_connected(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "connected"}
