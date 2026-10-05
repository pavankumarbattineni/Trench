import time
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select, update

from app.database.models import Provider, ProviderModel, Tenant, User
from app.database.session import async_session_factory
from tests.test_tenants_router import PREFIX as TENANT_PREFIX
from tests.test_tenants_router import (
    _auth_headers,
    _invite_and_accept,
    _signup_owner_and_create_tenant,
)

PREFIX = "test-users-router"
_EMAIL = f"owner@{PREFIX}.example.com"


def _claims() -> dict:
    return {
        "uid": f"{PREFIX}-uid",
        "email": _EMAIL,
        "iat": int(time.time()),
    }


async def _login(client: AsyncClient) -> None:
    """Signs up as an Owner (idempotently -- a 409 for an already-registered
    email is fine here) then signs in."""
    with patch("app.utils.firebase.verify_firebase_id_token", return_value=_claims()):
        await client.post(
            "/api/v1/auth/signup/owner",
            json={"id_token": "fake", "tenant_name": f"{PREFIX}-org"},
        )
        response = await client.post("/api/v1/auth/login", json={"id_token": "fake"})
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        await session.execute(
            update(User)
            .where(User.email.like(f"%{PREFIX}%"))
            .values(tenant_id=None)
        )
        await session.commit()
        tenant_result = await session.execute(
            select(Tenant).where(Tenant.domain.like(f"%{PREFIX}%"))
        )
        for tenant in tenant_result.scalars().all():
            await session.delete(tenant)
        await session.commit()
        result = await session.execute(
            select(User).where(User.email.like(f"%{PREFIX}%"))
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

        # Tenant-scope tests below reuse test_tenants_router's
        # helpers/PREFIX -- same cleanup pattern as test_credentials_router.py.
        await session.execute(
            update(User)
            .where(User.email.like(f"%{TENANT_PREFIX}%"))
            .values(tenant_id=None)
        )
        await session.commit()
        tenant_result = await session.execute(
            select(Tenant).where(Tenant.name.like(f"{TENANT_PREFIX}%"))
        )
        for tenant in tenant_result.scalars().all():
            await session.delete(tenant)
        await session.commit()
        await session.execute(delete(User).where(User.email.like(f"%{TENANT_PREFIX}%")))
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
    assert body["email"] == _EMAIL
    assert body["model_id"] is not None
    assert body["model_name"] is not None
    # Every user now belongs to a tenant (owner-signup creates
    # one) -- there is no more orgless state under the invitation-only model.
    assert body["tenant"]["role"] == "owner"
    # Owner access is role-derived, not grant-based.
    assert body["has_company_access"] is True
    assert body["personal_documents_uploaded_count"] == 0
    assert body["personal_document_limit"] == 5


@pytest.mark.asyncio
async def test_get_me_reflects_personal_documents_uploaded_count(client: AsyncClient):
    await _login(client)

    async with async_session_factory() as session:
        await session.execute(
            update(User)
            .where(User.email == _EMAIL)
            .values(documents_uploaded_count=3)
        )
        await session.commit()

    response = await client.get("/api/v1/users/me")
    assert response.status_code == 200
    assert response.json()["personal_documents_uploaded_count"] == 3


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


async def _openai_model_id() -> str:
    async with async_session_factory() as session:
        result = await session.execute(
            select(ProviderModel.id)
            .join(Provider, Provider.id == ProviderModel.provider_id)
            .where(Provider.name == "openai", ProviderModel.is_active.is_(True))
            .limit(1)
        )
        return str(result.scalar_one())


@pytest.mark.asyncio
async def test_update_model_validates_against_the_selected_scopes_credential(
    client: AsyncClient,
):
    """Selecting a BYOK model under knowledge_type=company must be checked
    against the tenant's own credential, not the caller's personal one --
    and vice versa -- matching how generation actually resolves the
    credential per scope (LLMClientService.resolve_for_knowledge)."""
    owner_token, tenant_id = await _signup_owner_and_create_tenant(client)
    openai_model_id = await _openai_model_id()

    # Neither the user nor the tenant has an OpenAI credential yet.
    personal_attempt = await client.patch(
        "/api/v1/users/me",
        json={"model_id": openai_model_id, "knowledge_type": "personal"},
        headers=_auth_headers(owner_token),
    )
    company_attempt = await client.patch(
        "/api/v1/users/me",
        json={"model_id": openai_model_id, "knowledge_type": "company"},
        headers=_auth_headers(owner_token),
    )
    assert personal_attempt.status_code == 422
    assert company_attempt.status_code == 422

    # Giving the tenant (not the user) a credential makes company-scope
    # selection succeed, while personal-scope selection still fails.
    with patch(
        "app.service.credential_service.CredentialValidationService.validate",
        new=AsyncMock(),
    ):
        await client.post(
            "/api/v1/credentials",
            json={
                "provider_type": "openai_llm",
                "api_key": "sk-tenant-test",
                "tenant_id": tenant_id,
            },
            headers=_auth_headers(owner_token),
        )

    company_attempt_after = await client.patch(
        "/api/v1/users/me",
        json={"model_id": openai_model_id, "knowledge_type": "company"},
        headers=_auth_headers(owner_token),
    )
    personal_attempt_after = await client.patch(
        "/api/v1/users/me",
        json={"model_id": openai_model_id, "knowledge_type": "personal"},
        headers=_auth_headers(owner_token),
    )
    assert company_attempt_after.status_code == 200
    assert personal_attempt_after.status_code == 422


@pytest.mark.asyncio
async def test_update_model_company_scope_requires_company_knowledge_access(
    client: AsyncClient,
):
    owner_token, tenant_id = await _signup_owner_and_create_tenant(client)
    member_token, member_user_id = await _invite_and_accept(
        client, tenant_id, owner_token, return_user_id=True
    )
    await client.post(
        f"/api/v1/tenants/{tenant_id}/knowledge-access",
        params={"user_id": member_user_id, "allow_access": False},
        headers=_auth_headers(owner_token),
    )
    openai_model_id = await _openai_model_id()

    response = await client.patch(
        "/api/v1/users/me",
        json={"model_id": openai_model_id, "knowledge_type": "company"},
        headers=_auth_headers(member_token),
    )
    assert response.status_code == 403
