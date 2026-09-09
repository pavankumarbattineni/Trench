import time
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import delete

from app.database.models import User
from app.database.session import async_session_factory

TEST_FIREBASE_UID = "test-firebase-uid-1"
TEST_EMAIL = "test.user@example.com"


def _fake_claims(
    uid: str = TEST_FIREBASE_UID, email: str = TEST_EMAIL, iat: int | None = None
) -> dict:
    return {
        "uid": uid,
        "email": email,
        "iat": iat if iat is not None else int(time.time()),
    }


@pytest.fixture(autouse=True)
async def cleanup_test_users():
    yield
    async with async_session_factory() as session:
        await session.execute(delete(User).where(User.firebase_uid.like("test-%")))
        await session.commit()


@pytest.mark.asyncio
async def test_session_creates_user_and_sets_cookies(client: AsyncClient):
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        response = await client.post("/auth/session", json={"id_token": "fake"})

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == TEST_EMAIL
    assert "access_token" in response.cookies
    assert "refresh_token" in response.cookies


@pytest.mark.asyncio
async def test_session_is_idempotent_for_same_firebase_uid(client: AsyncClient):
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        first = await client.post("/auth/session", json={"id_token": "fake"})
        second = await client.post("/auth/session", json={"id_token": "fake"})

    assert first.json()["id"] == second.json()["id"]


@pytest.mark.asyncio
async def test_session_uses_requested_username_on_first_signup(client: AsyncClient):
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        response = await client.post(
            "/auth/session", json={"id_token": "fake", "username": "test-chosen-name"}
        )

    assert response.status_code == 200
    assert response.json()["username"] == "test-chosen-name"


@pytest.mark.asyncio
async def test_session_rejects_duplicate_requested_username(client: AsyncClient):
    with patch(
        "app.utils.firebase.verify_firebase_id_token",
        return_value=_fake_claims(uid="test-first-owner"),
    ):
        first = await client.post(
            "/auth/session", json={"id_token": "fake", "username": "test-taken-name"}
        )
    assert first.status_code == 200

    with patch(
        "app.utils.firebase.verify_firebase_id_token",
        return_value=_fake_claims(
            uid="test-second-owner", email="test.other@example.com"
        ),
    ):
        second = await client.post(
            "/auth/session", json={"id_token": "fake", "username": "test-taken-name"}
        )

    assert second.status_code == 409


@pytest.mark.asyncio
async def test_session_rejects_malformed_username(client: AsyncClient):
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        response = await client.post(
            "/auth/session", json={"id_token": "fake", "username": "a"}
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_users_me_requires_authentication(client: AsyncClient):
    response = await client.get("/users/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_users_me_returns_profile_after_session(client: AsyncClient):
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        await client.post("/auth/session", json={"id_token": "fake"})

    response = await client.get("/users/me")
    assert response.status_code == 200
    assert response.json()["email"] == TEST_EMAIL


@pytest.mark.asyncio
async def test_refresh_rotates_tokens(client: AsyncClient):
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        await client.post("/auth/session", json={"id_token": "fake"})

    old_access = client.cookies.get("access_token")
    response = await client.post("/auth/refresh")

    assert response.status_code == 200
    assert client.cookies.get("access_token") != old_access


@pytest.mark.asyncio
async def test_refresh_without_cookie_is_unauthorized(client: AsyncClient):
    response = await client.post("/auth/refresh")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_logout_clears_session(client: AsyncClient):
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        await client.post("/auth/session", json={"id_token": "fake"})

    response = await client.post("/auth/logout")
    assert response.status_code == 204

    me = await client.get("/users/me")
    assert me.status_code == 401


@pytest.mark.asyncio
async def test_delete_account_removes_user_and_calls_firebase_delete(
    client: AsyncClient,
):
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        await client.post("/auth/session", json={"id_token": "fake"})

    with (
        patch(
            "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
        ),
        patch("app.utils.firebase.delete_firebase_user") as mock_delete,
    ):
        response = await client.request(
            "DELETE", "/auth/account", json={"id_token": "fake"}
        )

    assert response.status_code == 204
    mock_delete.assert_called_once_with(TEST_FIREBASE_UID)

    me = await client.get("/users/me")
    assert me.status_code == 401


@pytest.mark.asyncio
async def test_delete_account_rejects_stale_firebase_token(client: AsyncClient):
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        await client.post("/auth/session", json={"id_token": "fake"})

    stale_claims = _fake_claims(iat=int(time.time()) - 3600)
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=stale_claims
    ):
        response = await client.request(
            "DELETE", "/auth/account", json={"id_token": "fake"}
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_delete_account_rejects_token_for_different_account(client: AsyncClient):
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        await client.post("/auth/session", json={"id_token": "fake"})

    other_claims = _fake_claims(uid="test-someone-else")
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=other_claims
    ):
        response = await client.request(
            "DELETE", "/auth/account", json={"id_token": "fake"}
        )

    assert response.status_code == 403
