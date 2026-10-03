import time
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select, update

from app.database.models import Tenant, User
from app.database.session import async_session_factory
from tests.test_tenants_router import (
    PREFIX as TENANT_PREFIX,
)
from tests.test_tenants_router import (
    _auth_headers,
    _invite_and_accept,
    _signup_owner_and_create_tenant,
)

TEST_FIREBASE_UID = "test-credentials-router-uid"
TEST_EMAIL = "owner@test-credentials-router.example.com"
_TENANT_DOMAIN = "test-credentials-router.example.com"


def _fake_claims() -> dict:
    return {"uid": TEST_FIREBASE_UID, "email": TEST_EMAIL, "iat": int(time.time())}


async def _login(client: AsyncClient) -> None:
    """Signs up as an Owner (idempotently -- a 409 for an already-registered
    email is fine here) then signs in."""
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        await client.post(
            "/api/v1/auth/signup/owner",
            json={
                "id_token": "fake",
                "tenant_name": "test-credentials-router-org",
            },
        )
        response = await client.post("/api/v1/auth/login", json={"id_token": "fake"})
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        await session.execute(
            update(User).where(User.email == TEST_EMAIL).values(tenant_id=None)
        )
        await session.commit()
        tenant_result = await session.execute(
            select(Tenant).where(Tenant.domain == _TENANT_DOMAIN)
        )
        for tenant in tenant_result.scalars().all():
            await session.delete(tenant)
        await session.commit()
        result = await session.execute(select(User).where(User.email == TEST_EMAIL))
        user = result.scalar_one_or_none()
        if user is not None:
            await session.delete(user)
            await session.commit()

        # Tenant-credential tests below reuse test_tenants_router's
        # helpers/PREFIX -- same cleanup pattern as that file. Credentials
        # cascade with their tenant (tenant scope) or user (personal).
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
async def test_credentials_endpoints_require_authentication(client: AsyncClient):
    response = await client.get("/api/v1/credentials")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_save_list_and_delete_credential_via_api(client: AsyncClient):
    await _login(client)

    with patch(
        "app.service.credential_validation_service.CredentialValidationService.validate",
        return_value=None,
    ):
        save_response = await client.post(
            "/api/v1/credentials",
            json={"provider_type": "openai_llm", "api_key": "sk-test-key-0000"},
        )
    assert save_response.status_code == 204

    list_response = await client.get("/api/v1/credentials")
    assert list_response.status_code == 200
    credentials = list_response.json()["credentials"]
    assert len(credentials) == 1
    assert credentials[0]["provider_type"] == "openai_llm"
    assert credentials[0]["scope"] == "personal"
    assert "sk-test-key-0000" not in str(credentials[0])

    delete_response = await client.delete("/api/v1/credentials/openai_llm")
    assert delete_response.status_code == 204

    list_after_delete = await client.get("/api/v1/credentials")
    assert list_after_delete.json()["credentials"] == []


@pytest.mark.asyncio
async def test_save_credential_rejects_unknown_provider_type(client: AsyncClient):
    await _login(client)

    response = await client.post(
        "/api/v1/credentials",
        json={"provider_type": "not-a-real-provider", "api_key": "x"},
    )
    assert response.status_code == 422


# --- Tenant-scoped credentials, via the same unified API --------------


@pytest.mark.asyncio
async def test_only_owner_can_save_a_tenant_credential(client: AsyncClient):
    owner_token, tenant_id = await _signup_owner_and_create_tenant(client)
    admin_token = await _invite_and_accept(client, tenant_id, owner_token, role="admin")

    with patch(
        "app.service.credential_service.CredentialValidationService.validate",
        new=AsyncMock(),
    ):
        admin_attempt = await client.post(
            "/api/v1/credentials",
            json={
                "provider_type": "openai_llm",
                "api_key": "sk-test-admin",
                "tenant_id": tenant_id,
            },
            headers=_auth_headers(admin_token),
        )
        owner_attempt = await client.post(
            "/api/v1/credentials",
            json={
                "provider_type": "openai_llm",
                "api_key": "sk-test-owner",
                "tenant_id": tenant_id,
            },
            headers=_auth_headers(owner_token),
        )

    assert admin_attempt.status_code == 403
    assert owner_attempt.status_code == 204


@pytest.mark.asyncio
async def test_list_and_delete_tenant_credential_is_owner_only(
    client: AsyncClient,
):
    owner_token, tenant_id = await _signup_owner_and_create_tenant(client)
    admin_token = await _invite_and_accept(client, tenant_id, owner_token, role="admin")

    with patch(
        "app.service.credential_service.CredentialValidationService.validate",
        new=AsyncMock(),
    ):
        await client.post(
            "/api/v1/credentials",
            json={
                "provider_type": "openai_llm",
                "api_key": "sk-test-owner",
                "tenant_id": tenant_id,
            },
            headers=_auth_headers(owner_token),
        )

    admin_list = await client.get(
        "/api/v1/credentials",
        params={"tenant_id": tenant_id},
        headers=_auth_headers(admin_token),
    )
    owner_list = await client.get(
        "/api/v1/credentials",
        params={"tenant_id": tenant_id},
        headers=_auth_headers(owner_token),
    )
    assert admin_list.status_code == 403
    assert owner_list.status_code == 200
    owner_credentials = owner_list.json()["credentials"]
    assert len(owner_credentials) == 1
    assert owner_credentials[0]["scope"] == "tenant"

    admin_delete = await client.delete(
        "/api/v1/credentials/openai_llm",
        params={"tenant_id": tenant_id},
        headers=_auth_headers(admin_token),
    )
    owner_delete = await client.delete(
        "/api/v1/credentials/openai_llm",
        params={"tenant_id": tenant_id},
        headers=_auth_headers(owner_token),
    )
    assert admin_delete.status_code == 403
    assert owner_delete.status_code == 204


@pytest.mark.asyncio
async def test_personal_and_tenant_credentials_stay_separate(
    client: AsyncClient,
):
    """Saving a personal credential and a tenant credential for
    the same provider_type must not collide -- they're different rows,
    selected only by whether tenant_id is given."""
    owner_token, tenant_id = await _signup_owner_and_create_tenant(client)

    with patch(
        "app.service.credential_validation_service.CredentialValidationService.validate",
        new=AsyncMock(),
    ):
        await client.post(
            "/api/v1/credentials",
            json={"provider_type": "openai_llm", "api_key": "sk-test-personal"},
            headers=_auth_headers(owner_token),
        )
        await client.post(
            "/api/v1/credentials",
            json={
                "provider_type": "openai_llm",
                "api_key": "sk-test-tenant",
                "tenant_id": tenant_id,
            },
            headers=_auth_headers(owner_token),
        )

    personal_list = await client.get(
        "/api/v1/credentials", headers=_auth_headers(owner_token)
    )
    tenant_list = await client.get(
        "/api/v1/credentials",
        params={"tenant_id": tenant_id},
        headers=_auth_headers(owner_token),
    )
    assert len(personal_list.json()["credentials"]) == 1
    assert personal_list.json()["credentials"][0]["scope"] == "personal"
    assert len(tenant_list.json()["credentials"]) == 1
    assert tenant_list.json()["credentials"][0]["scope"] == "tenant"
