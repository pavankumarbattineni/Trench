import time
import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.database.models import Provider, ProviderModel, User
from app.database.session import async_session_factory

PREFIX = "test-users-router"


def _claims() -> dict:
    return {
        "uid": f"{PREFIX}-uid",
        "email": f"{PREFIX}@example.com",
        "iat": int(time.time()),
    }


async def _login(client: AsyncClient) -> None:
    """Signs up (idempotently -- a 409 for an already-registered email is
    fine here) then signs in, since login no longer lazily creates a
    user."""
    with patch("app.utils.firebase.verify_firebase_id_token", return_value=_claims()):
        await client.post("/api/v1/auth/signup", json={"id_token": "fake"})
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
        result = await session.execute(
            select(ProviderModel).where(
                ProviderModel.model_name.like(f"{PREFIX}%")
            )
        )
        for model in result.scalars().all():
            await session.delete(model)
        await session.commit()


async def _make_inactive_model() -> uuid.UUID:
    async with async_session_factory() as session:
        result = await session.execute(select(Provider).limit(1))
        provider = result.scalars().first()
        model = ProviderModel(
            provider_id=provider.id,
            model_name=f"{PREFIX}-inactive-model",
            display_name="Inactive Test Model",
            is_active=False,
            is_platform_default=False,
        )
        session.add(model)
        await session.commit()
        await session.refresh(model)
        return model.id


@pytest.mark.asyncio
async def test_get_me_requires_authentication(client: AsyncClient):
    response = await client.get("/api/v1/users/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_me_returns_profile_with_lazily_assigned_default_model(
    client: AsyncClient,
):
    await _login(client)

    response = await client.get("/api/v1/users/me")
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == f"{PREFIX}@example.com"
    assert body["model_id"] is not None
    assert body["model_name"] is not None
    assert body["organization"] is None
    assert body["has_company_access"] is False


@pytest.mark.asyncio
async def test_update_model_changes_selected_model(client: AsyncClient):
    await _login(client)

    async with async_session_factory() as session:
        result = await session.execute(
            select(ProviderModel).where(ProviderModel.is_active.is_(True)).limit(2)
        )
        models = result.scalars().all()
    assert len(models) >= 2, "need at least 2 active models seeded to test switching"
    target_model = next(m for m in models)

    response = await client.patch(
        "/api/v1/users/me", json={"model_id": str(target_model.id)}
    )
    assert response.status_code == 200
    assert response.json()["model_id"] == str(target_model.id)
    assert response.json()["model_name"] == target_model.display_name


@pytest.mark.asyncio
async def test_update_model_rejects_unknown_model_id(client: AsyncClient):
    await _login(client)

    response = await client.patch(
        "/api/v1/users/me", json={"model_id": str(uuid.uuid4())}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_model_rejects_inactive_model(client: AsyncClient):
    await _login(client)
    inactive_model_id = await _make_inactive_model()

    response = await client.patch(
        "/api/v1/users/me", json={"model_id": str(inactive_model_id)}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_update_model_requires_authentication(client: AsyncClient):
    response = await client.patch(
        "/api/v1/users/me", json={"model_id": str(uuid.uuid4())}
    )
    assert response.status_code == 401
