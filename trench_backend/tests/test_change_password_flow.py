import time
from unittest.mock import AsyncMock, Mock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, update

from app.database.models import Tenant, User
from app.database.session import async_session_factory

PREFIX = "test-change-password"


def _claims(email: str) -> dict:
    return {"uid": f"uid-{email}", "email": email, "iat": int(time.time())}


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
        await session.execute(
            delete(Tenant).where(Tenant.domain.like(f"%{PREFIX}%"))
        )
        await session.commit()
        await session.execute(delete(User).where(User.email.like(f"%{PREFIX}%")))
        await session.commit()


async def _login(client: AsyncClient, email: str, username: str) -> None:
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_claims(email)
    ):
        await client.post(
            "/api/v1/auth/signup/owner",
            json={
                "id_token": "fake",
                "username": username,
                "tenant_name": f"org-{username}",
            },
        )
        response = await client.post("/api/v1/auth/login", json={"id_token": "fake"})
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"


@pytest.mark.asyncio
async def test_change_password_succeeds_when_current_password_is_correct(
    client: AsyncClient,
):
    email = f"{PREFIX}-correct@{PREFIX}-correct.example.com"
    await _login(client, email, f"{PREFIX}-correct")

    with (
        patch(
            "app.service.auth_service.firebase_utils.verify_user_password",
            new=AsyncMock(return_value=True),
        ) as mock_verify,
        patch(
            "app.service.auth_service.firebase_utils.set_user_password",
            new=Mock(),
        ) as mock_set_pw,
    ):
        response = await client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": "old-strong-password-1",
                "new_password": "a-new-strong-password-1",
                "confirm_new_password": "a-new-strong-password-1",
            },
        )

    assert response.status_code == 200, response.text
    mock_verify.assert_awaited_once_with(email, "old-strong-password-1")
    mock_set_pw.assert_called_once_with(email, "a-new-strong-password-1")


@pytest.mark.asyncio
async def test_change_password_rejects_an_incorrect_current_password(
    client: AsyncClient,
):
    email = f"{PREFIX}-wrong@{PREFIX}-wrong.example.com"
    await _login(client, email, f"{PREFIX}-wrong")

    with (
        patch(
            "app.service.auth_service.firebase_utils.verify_user_password",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.service.auth_service.firebase_utils.set_user_password",
            new=Mock(),
        ) as mock_set_pw,
    ):
        response = await client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": "totally-wrong-password",
                "new_password": "a-new-strong-password-1",
                "confirm_new_password": "a-new-strong-password-1",
            },
        )

    assert response.status_code == 400
    mock_set_pw.assert_not_called()


@pytest.mark.asyncio
async def test_change_password_rejects_a_mismatched_confirm_password(
    client: AsyncClient,
):
    email = f"{PREFIX}-mismatch@{PREFIX}-mismatch.example.com"
    await _login(client, email, f"{PREFIX}-mismatch")

    with patch(
        "app.service.auth_service.firebase_utils.set_user_password",
        new=Mock(),
    ) as mock_set_pw:
        response = await client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": "old-strong-password-1",
                "new_password": "a-new-strong-password-1",
                "confirm_new_password": "a-different-password-2",
            },
        )

    assert response.status_code == 422
    mock_set_pw.assert_not_called()


@pytest.mark.asyncio
async def test_change_password_rejects_a_short_new_password(client: AsyncClient):
    email = f"{PREFIX}-short@{PREFIX}-short.example.com"
    await _login(client, email, f"{PREFIX}-short")

    response = await client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": "old-strong-password-1",
            "new_password": "short",
            "confirm_new_password": "short",
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_change_password_requires_authentication(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": "old-strong-password-1",
            "new_password": "a-new-strong-password-1",
            "confirm_new_password": "a-new-strong-password-1",
        },
    )

    assert response.status_code == 401
