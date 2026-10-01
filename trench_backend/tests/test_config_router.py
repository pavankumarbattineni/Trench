import time
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.database.models import Organization, User
from app.database.session import async_session_factory

PREFIX = "test-config-router"
_EMAIL = f"owner@{PREFIX}.example.com"


def _claims() -> dict:
    return {
        "uid": f"{PREFIX}-uid",
        "email": _EMAIL,
        "iat": int(time.time()),
    }


async def _login(client: AsyncClient) -> None:
    """Signs up as an Owner (idempotently -- a 409 for an already-registered
    email is fine here, since signup/invite-accept is now the only way a
    user and its organization come into being) then signs in."""
    with patch("app.utils.firebase.verify_firebase_id_token", return_value=_claims()):
        await client.post(
            "/api/v1/auth/signup/owner",
            json={"id_token": "fake", "organization_name": f"{PREFIX}-org"},
        )
        response = await client.post("/api/v1/auth/login", json={"id_token": "fake"})
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        # users.organization_id <-> organizations.owner_user_id is a
        # circular FK -- clear the user side before deleting the org.
        await session.execute(
            update(User)
            .where(User.email.like(f"%{PREFIX}%"))
            .values(organization_id=None)
        )
        await session.commit()
        org_result = await session.execute(
            select(Organization).where(Organization.domain.like(f"%{PREFIX}%"))
        )
        for organization in org_result.scalars().all():
            await session.delete(organization)
        await session.commit()
        result = await session.execute(
            select(User).where(User.email.like(f"%{PREFIX}%"))
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
