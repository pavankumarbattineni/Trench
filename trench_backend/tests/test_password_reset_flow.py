import time
import uuid
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select, update

from app.database.models import Organization, PasswordResetToken, User
from app.database.session import async_session_factory

PREFIX = "test-password-reset"


def _fake_claims(email: str) -> dict:
    return {"uid": f"uid-{email}", "email": email, "iat": int(time.time())}


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        await session.execute(delete(PasswordResetToken))
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
        await session.execute(delete(User).where(User.email.like(f"%{PREFIX}%")))
        await session.commit()


async def _signup(client: AsyncClient, email: str, username: str) -> None:
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims(email)
    ):
        response = await client.post(
            "/api/v1/auth/signup/owner",
            json={
                "id_token": "fake",
                "username": username,
                "organization_name": f"org-{uuid.uuid4().hex[:8]}",
            },
        )
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_request_reset_always_returns_200_even_for_unknown_email(
    client: AsyncClient,
):
    with patch(
        "app.service.password_reset_service.send_email", new=AsyncMock()
    ) as mock_send:
        response = await client.post(
            "/api/v1/auth/password-reset/request",
            json={"email": f"{PREFIX}-doesnotexist@{PREFIX}-doesnotexist.example.com"},
        )
    assert response.status_code == 200
    mock_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_reset_emails_a_token_for_a_known_user(client: AsyncClient):
    email = f"{PREFIX}-resetme@{PREFIX}-resetme.example.com"
    await _signup(client, email, f"{PREFIX}-resetme")

    with patch(
        "app.service.password_reset_service.send_email", new=AsyncMock()
    ) as mock_send:
        response = await client.post(
            "/api/v1/auth/password-reset/request", json={"email": email}
        )
    assert response.status_code == 200
    mock_send.assert_awaited_once()


async def _request_reset_and_capture_token(client: AsyncClient, email: str) -> str:
    captured = {}

    async def _capture_send(*, to, subject, html_body, text_body):
        captured["html_body"] = html_body

    with patch(
        "app.service.password_reset_service.send_email", new=_capture_send
    ):
        await client.post(
            "/api/v1/auth/password-reset/request", json={"email": email}
        )

    accept_url = captured["html_body"].split('href="')[1].split('"')[0]
    return parse_qs(urlparse(accept_url).query)["token"][0]


@pytest.mark.asyncio
async def test_confirm_reset_updates_password_via_firebase_admin_sdk(
    client: AsyncClient,
):
    email = f"{PREFIX}-confirmme@{PREFIX}-confirmme.example.com"
    await _signup(client, email, f"{PREFIX}-confirmme")
    raw_token = await _request_reset_and_capture_token(client, email)

    with patch("app.utils.firebase.set_user_password", new=Mock()) as mock_set_pw:
        response = await client.post(
            "/api/v1/auth/password-reset/confirm",
            json={
                "token": raw_token,
                "new_password": "a-new-strong-password-1",
                "confirm_password": "a-new-strong-password-1",
            },
        )
    assert response.status_code == 200
    mock_set_pw.assert_called_once_with(email, "a-new-strong-password-1")


@pytest.mark.asyncio
async def test_confirm_reset_rejects_a_reused_token(client: AsyncClient):
    email = f"{PREFIX}-reuseme@{PREFIX}-reuseme.example.com"
    await _signup(client, email, f"{PREFIX}-reuseme")
    raw_token = await _request_reset_and_capture_token(client, email)

    with patch("app.utils.firebase.set_user_password", new=Mock()):
        first = await client.post(
            "/api/v1/auth/password-reset/confirm",
            json={
                "token": raw_token,
                "new_password": "a-new-strong-password-1",
                "confirm_password": "a-new-strong-password-1",
            },
        )
        second = await client.post(
            "/api/v1/auth/password-reset/confirm",
            json={
                "token": raw_token,
                "new_password": "another-password-2",
                "confirm_password": "another-password-2",
            },
        )
    assert first.status_code == 200
    assert second.status_code == 404


@pytest.mark.asyncio
async def test_confirm_reset_rejects_an_unknown_token(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={
            "token": "not-a-real-token",
            "new_password": "a-new-strong-password-1",
            "confirm_password": "a-new-strong-password-1",
        },
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_confirm_reset_rejects_a_mismatched_confirm_password(
    client: AsyncClient,
):
    email = f"{PREFIX}-mismatchpw@{PREFIX}-mismatchpw.example.com"
    await _signup(client, email, f"{PREFIX}-mismatchpw")
    raw_token = await _request_reset_and_capture_token(client, email)

    with patch("app.utils.firebase.set_user_password", new=Mock()) as mock_set_pw:
        response = await client.post(
            "/api/v1/auth/password-reset/confirm",
            json={
                "token": raw_token,
                "new_password": "a-new-strong-password-1",
                "confirm_password": "a-different-password-2",
            },
        )
    assert response.status_code == 422
    mock_set_pw.assert_not_called()


@pytest.mark.asyncio
async def test_confirm_reset_rejects_a_short_password(client: AsyncClient):
    email = f"{PREFIX}-shortpw@{PREFIX}-shortpw.example.com"
    await _signup(client, email, f"{PREFIX}-shortpw")
    raw_token = await _request_reset_and_capture_token(client, email)

    response = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={
            "token": raw_token,
            "new_password": "short",
            "confirm_password": "short",
        },
    )
    assert response.status_code == 422
