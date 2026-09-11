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


def _auth_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


@pytest.fixture(autouse=True)
async def cleanup_test_users():
    yield
    async with async_session_factory() as session:
        await session.execute(delete(User).where(User.email.like("test%@example.com")))
        await session.commit()


@pytest.mark.asyncio
async def test_login_returns_a_bearer_token_pair(client: AsyncClient):
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        response = await client.post("/api/v1/auth/login", json={"id_token": "fake"})

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]
    # No cookies -- tokens are returned in the body for the frontend to
    # store and attach itself (see app/router/deps.py, HTTPBearer).
    assert not response.cookies


@pytest.mark.asyncio
async def test_session_is_idempotent_for_same_firebase_uid(client: AsyncClient):
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        first = await client.post("/api/v1/auth/login", json={"id_token": "fake"})
        second = await client.post("/api/v1/auth/login", json={"id_token": "fake"})

    first_me = await client.get(
        "/api/v1/users/me", headers=_auth_headers(first.json()["access_token"])
    )
    second_me = await client.get(
        "/api/v1/users/me", headers=_auth_headers(second.json()["access_token"])
    )
    assert first_me.json()["id"] == second_me.json()["id"]


@pytest.mark.asyncio
async def test_session_uses_requested_username_on_first_signup(client: AsyncClient):
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        login_response = await client.post(
            "/api/v1/auth/login",
            json={"id_token": "fake", "username": "test-chosen-name"},
        )

    me = await client.get(
        "/api/v1/users/me",
        headers=_auth_headers(login_response.json()["access_token"]),
    )
    assert me.json()["username"] == "test-chosen-name"


@pytest.mark.asyncio
async def test_session_rejects_duplicate_requested_username(client: AsyncClient):
    with patch(
        "app.utils.firebase.verify_firebase_id_token",
        return_value=_fake_claims(uid="test-first-owner"),
    ):
        first = await client.post(
            "/api/v1/auth/login",
            json={"id_token": "fake", "username": "test-taken-name"},
        )
    assert first.status_code == 200

    with patch(
        "app.utils.firebase.verify_firebase_id_token",
        return_value=_fake_claims(
            uid="test-second-owner", email="test.other@example.com"
        ),
    ):
        second = await client.post(
            "/api/v1/auth/login",
            json={"id_token": "fake", "username": "test-taken-name"},
        )

    assert second.status_code == 409


@pytest.mark.asyncio
async def test_session_rejects_malformed_username(client: AsyncClient):
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        response = await client.post(
            "/api/v1/auth/login", json={"id_token": "fake", "username": "a"}
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_users_me_requires_authentication(client: AsyncClient):
    response = await client.get("/api/v1/users/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_users_me_returns_profile_after_session(client: AsyncClient):
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        login_response = await client.post(
            "/api/v1/auth/login", json={"id_token": "fake"}
        )

    response = await client.get(
        "/api/v1/users/me",
        headers=_auth_headers(login_response.json()["access_token"]),
    )
    assert response.status_code == 200
    assert response.json()["email"] == TEST_EMAIL


@pytest.mark.asyncio
async def test_refresh_rotates_tokens(client: AsyncClient):
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        login_response = await client.post(
            "/api/v1/auth/login", json={"id_token": "fake"}
        )
    old_access = login_response.json()["access_token"]

    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": login_response.json()["refresh_token"]},
    )

    assert response.status_code == 200
    assert response.json()["access_token"] != old_access


@pytest.mark.asyncio
async def test_refresh_without_a_token_is_unauthorized(client: AsyncClient):
    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": ""})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_delete_account_removes_user_and_calls_firebase_delete(
    client: AsyncClient,
):
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        login_response = await client.post(
            "/api/v1/auth/login", json={"id_token": "fake"}
        )
    access_token = login_response.json()["access_token"]

    with (
        patch(
            "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
        ),
        patch("app.utils.firebase.delete_firebase_user") as mock_delete,
    ):
        response = await client.request(
            "DELETE",
            "/api/v1/auth/account",
            json={"id_token": "fake"},
            headers=_auth_headers(access_token),
        )

    assert response.status_code == 204
    mock_delete.assert_called_once_with(TEST_FIREBASE_UID)

    me = await client.get("/api/v1/users/me", headers=_auth_headers(access_token))
    assert me.status_code == 401


@pytest.mark.asyncio
async def test_delete_account_rejects_stale_firebase_token(client: AsyncClient):
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        login_response = await client.post(
            "/api/v1/auth/login", json={"id_token": "fake"}
        )
    access_token = login_response.json()["access_token"]

    stale_claims = _fake_claims(iat=int(time.time()) - 3600)
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=stale_claims
    ):
        response = await client.request(
            "DELETE",
            "/api/v1/auth/account",
            json={"id_token": "fake"},
            headers=_auth_headers(access_token),
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_delete_account_rejects_token_for_different_account(client: AsyncClient):
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        login_response = await client.post(
            "/api/v1/auth/login", json={"id_token": "fake"}
        )
    access_token = login_response.json()["access_token"]

    # A genuinely different account now means a different email -- Trench
    # correlates identity by email, not by storing Firebase's own UID.
    other_claims = _fake_claims(
        uid="test-someone-else", email="test-someone-else@example.com"
    )
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=other_claims
    ):
        response = await client.request(
            "DELETE",
            "/api/v1/auth/account",
            json={"id_token": "fake"},
            headers=_auth_headers(access_token),
        )

    assert response.status_code == 403
