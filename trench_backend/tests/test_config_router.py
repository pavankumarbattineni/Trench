import time
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.database.models import User
from app.database.session import async_session_factory

PREFIX = "test-config-router"


def _claims() -> dict:
    return {
        "uid": f"{PREFIX}-uid",
        "email": f"{PREFIX}@example.com",
        "iat": int(time.time()),
    }


async def _login(client: AsyncClient) -> None:
    with patch("app.utils.firebase.verify_firebase_id_token", return_value=_claims()):
        response = await client.post("/api/v1/auth/login", json={"id_token": "fake"})
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.email.like(f"{PREFIX}%"))
        )
        for user in result.scalars().all():
            await session.delete(user)
        await session.commit()


@pytest.mark.asyncio
async def test_config_requires_authentication(client: AsyncClient):
    response = await client.get("/api/v1/config")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_config_returns_llm_models(client: AsyncClient):
    await _login(client)

    response = await client.get("/api/v1/config")
    assert response.status_code == 200
    body = response.json()

    assert body["llm_models"]
    assert sum(model["is_platform_default"] for model in body["llm_models"]) == 1
    provider_names = {model["provider_name"] for model in body["llm_models"]}
    assert {"groq", "openai", "anthropic", "google"}.issubset(provider_names)
