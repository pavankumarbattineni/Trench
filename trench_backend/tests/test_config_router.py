import time
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select, update

from app.database.models import Tenant, User
from app.database.session import async_session_factory
from tests.test_tenants_router import PREFIX as TENANT_PREFIX
from tests.test_tenants_router import (
    _auth_headers,
    _invite_and_accept,
    _signup_owner_and_create_tenant,
)

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
    user and its tenant come into being) then signs in."""
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
        # users.tenant_id has no ondelete -- clear it before deleting the
        # tenant.
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
        await session.commit()

        # Tenant-scope tests below reuse test_tenants_router's helpers/PREFIX
        # -- same cleanup pattern as test_credentials_router.py.
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


@pytest.mark.asyncio
async def test_personal_byok_credential_does_not_leak_into_company_scope(
    client: AsyncClient,
):
    """A personal OpenAI key must only make OpenAI models show as
    available under knowledge_type=personal -- not company, even for the
    same user/request -- since generation never uses a member's personal
    key for a company query (see LLMClientService.resolve_for_knowledge)."""
    owner_token, tenant_id = await _signup_owner_and_create_tenant(client)

    with patch(
        "app.service.credential_service.CredentialValidationService.validate",
        new=AsyncMock(),
    ):
        save_response = await client.post(
            "/api/v1/credentials",
            json={"provider_type": "openai_llm", "api_key": "sk-personal-test"},
            headers=_auth_headers(owner_token),
        )
    assert save_response.status_code == 204

    personal_response = await client.get(
        "/api/v1/config",
        params={"knowledge_type": "personal"},
        headers=_auth_headers(owner_token),
    )
    company_response = await client.get(
        "/api/v1/config",
        params={"knowledge_type": "company"},
        headers=_auth_headers(owner_token),
    )
    assert personal_response.status_code == 200
    assert company_response.status_code == 200

    def _openai_has_credential(body: dict) -> bool:
        return next(
            m["has_credential"]
            for m in body["llm_models"]
            if m["provider_name"] == "openai"
        )

    assert _openai_has_credential(personal_response.json()) is True
    assert _openai_has_credential(company_response.json()) is False

    # Once the tenant (not the user) has its own OpenAI credential, company
    # scope reflects *that* -- still independent of the user's own key.
    with patch(
        "app.service.credential_service.CredentialValidationService.validate",
        new=AsyncMock(),
    ):
        tenant_save_response = await client.post(
            "/api/v1/credentials",
            json={
                "provider_type": "openai_llm",
                "api_key": "sk-tenant-test",
                "tenant_id": tenant_id,
            },
            headers=_auth_headers(owner_token),
        )
    assert tenant_save_response.status_code == 204

    company_response_after = await client.get(
        "/api/v1/config",
        params={"knowledge_type": "company"},
        headers=_auth_headers(owner_token),
    )
    assert _openai_has_credential(company_response_after.json()) is True


@pytest.mark.asyncio
async def test_company_scope_requires_company_knowledge_access(client: AsyncClient):
    owner_token, tenant_id = await _signup_owner_and_create_tenant(client)
    member_token, member_user_id = await _invite_and_accept(
        client, tenant_id, owner_token, return_user_id=True
    )
    # Members are granted company-knowledge access by default on accept --
    # revoke it to exercise the unauthorized case.
    revoke_response = await client.post(
        f"/api/v1/tenants/{tenant_id}/knowledge-access",
        params={"user_id": member_user_id, "allow_access": False},
        headers=_auth_headers(owner_token),
    )
    assert revoke_response.status_code == 200

    response = await client.get(
        "/api/v1/config",
        params={"knowledge_type": "company"},
        headers=_auth_headers(member_token),
    )
    assert response.status_code == 403
