import time
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.database.models import User
from app.database.session import async_session_factory

TEST_FIREBASE_UID = "test-credentials-router-uid"
TEST_EMAIL = "test-credentials-router@example.com"


def _fake_claims() -> dict:
    return {"uid": TEST_FIREBASE_UID, "email": TEST_EMAIL, "iat": int(time.time())}


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.firebase_uid == TEST_FIREBASE_UID)
        )
        user = result.scalar_one_or_none()
        if user is not None:
            await session.delete(user)
            await session.commit()


@pytest.mark.asyncio
async def test_credentials_endpoints_require_authentication(client: AsyncClient):
    response = await client.get("/credentials")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_save_list_and_delete_credential_via_api(client: AsyncClient):
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        await client.post("/auth/session", json={"id_token": "fake"})

    with patch(
        "app.service.credential_validation_service.CredentialValidationService.validate",
        return_value=None,
    ):
        save_response = await client.post(
            "/credentials",
            json={"provider_type": "openai_llm", "api_key": "sk-test-key-0000"},
        )
    assert save_response.status_code == 204

    list_response = await client.get("/credentials")
    assert list_response.status_code == 200
    credentials = list_response.json()["credentials"]
    assert len(credentials) == 1
    assert credentials[0]["provider_type"] == "openai_llm"
    assert "sk-test-key-0000" not in str(credentials[0])

    delete_response = await client.delete("/credentials/openai_llm")
    assert delete_response.status_code == 204

    list_after_delete = await client.get("/credentials")
    assert list_after_delete.json()["credentials"] == []


@pytest.mark.asyncio
async def test_save_credential_rejects_unknown_provider_type(client: AsyncClient):
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        await client.post("/auth/session", json={"id_token": "fake"})

    response = await client.post(
        "/credentials", json={"provider_type": "not-a-real-provider", "api_key": "x"}
    )
    assert response.status_code == 422
