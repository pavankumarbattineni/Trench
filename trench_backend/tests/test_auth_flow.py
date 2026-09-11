import time
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select

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


async def _signup(
    client: AsyncClient,
    *,
    uid: str = TEST_FIREBASE_UID,
    email: str = TEST_EMAIL,
    username: str | None = None,
    register_as_admin: bool = False,
):
    body = {"id_token": "fake", "register_as_admin": register_as_admin}
    if username is not None:
        body["username"] = username
    with patch(
        "app.utils.firebase.verify_firebase_id_token",
        return_value=_fake_claims(uid=uid, email=email),
    ):
        return await client.post("/api/v1/auth/signup", json=body)


async def _login(
    client: AsyncClient, *, uid: str = TEST_FIREBASE_UID, email: str = TEST_EMAIL
):
    with patch(
        "app.utils.firebase.verify_firebase_id_token",
        return_value=_fake_claims(uid=uid, email=email),
    ):
        return await client.post("/api/v1/auth/login", json={"id_token": "fake"})


async def _signup_and_login(client: AsyncClient, **kwargs) -> str:
    """Signs up then signs in, returning the access token. Splits kwargs
    between the two calls by name (`username`/`register_as_admin` only
    apply to signup)."""
    signup_kwargs = {
        k: v for k, v in kwargs.items() if k in ("username", "register_as_admin")
    }
    login_kwargs = {k: v for k, v in kwargs.items() if k in ("uid", "email")}
    signup_response = await _signup(client, **signup_kwargs, **login_kwargs)
    assert signup_response.status_code == 200, signup_response.text
    login_response = await _login(client, **login_kwargs)
    assert login_response.status_code == 200, login_response.text
    return login_response.json()["access_token"]


@pytest.fixture(autouse=True)
async def cleanup_test_users():
    yield
    async with async_session_factory() as session:
        await session.execute(delete(User).where(User.email.like("test%@example.com")))
        await session.commit()


# --- Signup ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_signup_creates_a_user_without_issuing_tokens(client: AsyncClient):
    response = await _signup(client)

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == TEST_EMAIL
    assert body["role"] == "user"
    assert "access_token" not in body
    assert "refresh_token" not in body


@pytest.mark.asyncio
async def test_signup_rejects_an_already_registered_email(client: AsyncClient):
    first = await _signup(client)
    assert first.status_code == 200

    second = await _signup(client)
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_signup_uses_the_requested_username(client: AsyncClient):
    response = await _signup(client, username="test-chosen-name")
    assert response.status_code == 200
    assert response.json()["username"] == "test-chosen-name"


@pytest.mark.asyncio
async def test_signup_rejects_duplicate_requested_username(client: AsyncClient):
    first = await _signup(
        client, uid="test-first-owner", email="test.first@example.com",
        username="test-taken-name",
    )
    assert first.status_code == 200

    second = await _signup(
        client, uid="test-second-owner", email="test.other@example.com",
        username="test-taken-name",
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_signup_rejects_malformed_username(client: AsyncClient):
    response = await _signup(client, username="a")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_signup_default_role_is_user(client: AsyncClient):
    response = await _signup(client)
    assert response.json()["role"] == "user"


@pytest.mark.asyncio
async def test_signup_cannot_self_assign_admin_role_directly(client: AsyncClient):
    """There is no raw `role` field on the signup request -- only the
    gated `register_as_admin` boolean -- so a client attempting to smuggle
    role="admin" directly has no effect; it's simply an unknown field."""
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        response = await client.post(
            "/api/v1/auth/signup", json={"id_token": "fake", "role": "admin"}
        )
    assert response.status_code == 200
    assert response.json()["role"] == "user"


@pytest.mark.asyncio
async def test_register_as_admin_becomes_admin_only_when_none_exists_yet(
    client: AsyncClient,
):
    admin_status = await client.get("/api/v1/auth/admin-status")
    if admin_status.json()["admin_exists"]:
        pytest.skip(
            "an administrator already exists elsewhere in this shared dev "
            "database -- the 'no admin exists yet' branch can't be "
            "deterministically exercised here; the converse branch is "
            "covered by test_register_as_admin_is_ignored_once_one_exists"
        )

    response = await _signup(client, register_as_admin=True)
    assert response.status_code == 200
    assert response.json()["role"] == "admin"


@pytest.mark.asyncio
async def test_register_as_admin_is_ignored_once_one_exists(client: AsyncClient):
    # Manufacture the "an admin already exists" precondition ourselves,
    # deterministically, regardless of the database's real-world state.
    first = await _signup(
        client, uid="test-existing-admin", email="test.existing-admin@example.com",
        register_as_admin=True,
    )
    assert first.status_code == 200
    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.email == "test.existing-admin@example.com")
        )
        user = result.scalar_one()
        if user.role != "admin":
            user.role = "admin"
            await session.commit()

    second = await _signup(
        client, uid="test-second-admin-hopeful",
        email="test.second-admin-hopeful@example.com", register_as_admin=True,
    )
    assert second.status_code == 200
    assert second.json()["role"] == "user"


# --- Signin (login) ---------------------------------------------------------


@pytest.mark.asyncio
async def test_login_rejects_an_unregistered_user(client: AsyncClient):
    response = await _login(client)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_signup_then_login_returns_a_bearer_token_pair(client: AsyncClient):
    assert (await _signup(client)).status_code == 200

    response = await _login(client)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]
    # No cookies -- tokens are returned in the body for the frontend to
    # store and attach itself (see app/router/deps.py, HTTPBearer).
    assert not response.cookies


@pytest.mark.asyncio
async def test_login_is_idempotent_across_repeated_signins(client: AsyncClient):
    assert (await _signup(client)).status_code == 200

    first = await _login(client)
    second = await _login(client)

    first_me = await client.get(
        "/api/v1/users/me", headers=_auth_headers(first.json()["access_token"])
    )
    second_me = await client.get(
        "/api/v1/users/me", headers=_auth_headers(second.json()["access_token"])
    )
    assert first_me.json()["id"] == second_me.json()["id"]


@pytest.mark.asyncio
async def test_users_me_requires_authentication(client: AsyncClient):
    response = await client.get("/api/v1/users/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_users_me_returns_profile_after_signin(client: AsyncClient):
    access_token = await _signup_and_login(client)

    response = await client.get(
        "/api/v1/users/me", headers=_auth_headers(access_token)
    )
    assert response.status_code == 200
    assert response.json()["email"] == TEST_EMAIL
    assert response.json()["role"] == "user"


@pytest.mark.asyncio
async def test_refresh_rotates_tokens(client: AsyncClient):
    await _signup(client)
    login_response = await _login(client)
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


# --- Account deletion --------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_account_removes_user_and_calls_firebase_delete(
    client: AsyncClient,
):
    access_token = await _signup_and_login(client)

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
    access_token = await _signup_and_login(client)

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
    access_token = await _signup_and_login(client)

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
