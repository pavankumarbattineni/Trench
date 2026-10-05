import time
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select, update

from app.database.models import Tenant, User
from app.database.session import async_session_factory

TEST_FIREBASE_UID = "test-firebase-uid-1"
TEST_EMAIL = "test.user@example.com"


class _FakeVectorStore:
    def __init__(self) -> None:
        self.delete_namespace = AsyncMock()


@pytest.fixture(autouse=True)
def _fake_vector_store(monkeypatch):
    """delete_account now best-effort wipes the caller's personal Pinecone
    namespace -- avoid a real Pinecone call in the automated test suite."""
    fake = _FakeVectorStore()
    monkeypatch.setattr(
        "app.service.auth_service.get_vector_store", lambda **kwargs: fake
    )
    return fake


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


async def _signup_owner(
    client: AsyncClient,
    *,
    uid: str = TEST_FIREBASE_UID,
    email: str = TEST_EMAIL,
    username: str | None = None,
    tenant_name: str | None = None,
):
    body = {
        "id_token": "fake",
        "tenant_name": tenant_name or f"test-org-{uuid.uuid4().hex[:8]}",
    }
    if username is not None:
        body["username"] = username
    with patch(
        "app.utils.firebase.verify_firebase_id_token",
        return_value=_fake_claims(uid=uid, email=email),
    ):
        return await client.post("/api/v1/auth/signup/owner", json=body)


async def _login(
    client: AsyncClient, *, uid: str = TEST_FIREBASE_UID, email: str = TEST_EMAIL
):
    with patch(
        "app.utils.firebase.verify_firebase_id_token",
        return_value=_fake_claims(uid=uid, email=email),
    ):
        return await client.post("/api/v1/auth/login", json={"id_token": "fake"})


async def _signup_owner_and_get_token(client: AsyncClient, **kwargs) -> str:
    """Signs up an Owner (which no longer issues a session itself) then
    logs in separately, returning the access token."""
    signup_kwargs = {
        k: v for k, v in kwargs.items() if k in ("username", "tenant_name")
    }
    login_kwargs = {k: v for k, v in kwargs.items() if k in ("uid", "email")}
    response = await _signup_owner(client, **signup_kwargs, **login_kwargs)
    assert response.status_code == 200, response.text
    login_response = await _login(client, **login_kwargs)
    assert login_response.status_code == 200, login_response.text
    return login_response.json()["access_token"]


@pytest.fixture(autouse=True)
async def cleanup_test_users():
    yield
    async with async_session_factory() as session:
        await session.execute(
            update(User)
            .where(User.email.like("test%@example.com"))
            .values(tenant_id=None)
        )
        await session.commit()
        tenant_result = await session.execute(
            select(Tenant).where(Tenant.domain == "example.com")
        )
        for tenant in tenant_result.scalars().all():
            await session.delete(tenant)
        await session.commit()
        await session.execute(delete(User).where(User.email.like("test%@example.com")))
        await session.commit()


# --- Owner signup -------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_signup_does_not_issue_a_session_but_creates_a_tenant(
    client: AsyncClient,
):
    """Owner-signup no longer logs the new Owner straight into the app --
    the frontend sends them to /signin instead, same as every other
    account-creation path except invite-accept (which stays untouched)."""
    response = await _signup_owner(client, tenant_name="Test Acme Corp")

    assert response.status_code == 200
    body = response.json()
    assert "access_token" not in body
    assert "refresh_token" not in body
    assert body["tenant"]["name"] == "Test Acme Corp"
    assert body["tenant"]["domain"] == "example.com"

    login = await _login(client)
    assert login.status_code == 200
    me = await client.get(
        "/api/v1/users/me", headers=_auth_headers(login.json()["access_token"])
    )
    assert me.json()["email"] == TEST_EMAIL
    assert me.json()["tenant"]["role"] == "owner"


@pytest.mark.asyncio
async def test_owner_signup_rejects_an_already_registered_email(client: AsyncClient):
    first = await _signup_owner(client)
    assert first.status_code == 200

    second = await _signup_owner(client)
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_owner_signup_rejects_a_public_email_domain(client: AsyncClient):
    response = await _signup_owner(
        client,
        uid="test-public-domain-owner",
        email="test.someone@gmail.com",
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_owner_signup_uses_the_requested_username(client: AsyncClient):
    access_token = await _signup_owner_and_get_token(
        client, username="test-chosen-name"
    )
    me = await client.get("/api/v1/users/me", headers=_auth_headers(access_token))
    assert me.json()["username"] == "test-chosen-name"


@pytest.mark.asyncio
async def test_owner_signup_rejects_duplicate_requested_username(
    client: AsyncClient,
):
    first = await _signup_owner(
        client,
        uid="test-first-owner",
        email="test.first@example.com",
        username="test-taken-name",
    )
    assert first.status_code == 200

    second = await _signup_owner(
        client,
        uid="test-second-owner",
        email="test.other@example.com",
        username="test-taken-name",
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_owner_signup_rejects_malformed_username(client: AsyncClient):
    response = await _signup_owner(client, username="a")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_owner_signup_default_role_is_owner_not_a_client_supplied_value(
    client: AsyncClient,
):
    """There is no raw `role` field on OwnerSignupRequest at all -- a
    client attempting to smuggle one has no effect; the Owner role comes
    only from being the one who calls this endpoint."""
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        response = await client.post(
            "/api/v1/auth/signup/owner",
            json={
                "id_token": "fake",
                "tenant_name": "Test Org",
                "role": "superadmin",
            },
        )
    assert response.status_code == 200
    login = await _login(client)
    me = await client.get(
        "/api/v1/users/me",
        headers=_auth_headers(login.json()["access_token"]),
    )
    assert me.json()["tenant"]["role"] == "owner"
    # There's no separate top-level app role anymore -- the tenant role is
    # the user's only role.
    assert "role" not in me.json()


# --- Signin (login) ---------------------------------------------------------


@pytest.mark.asyncio
async def test_login_rejects_an_unregistered_user(client: AsyncClient):
    response = await _login(client)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_login_after_owner_signup_returns_a_bearer_token_pair(
    client: AsyncClient,
):
    assert (await _signup_owner(client)).status_code == 200

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
    assert (await _signup_owner(client)).status_code == 200

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
async def test_users_me_returns_profile_after_owner_signup(client: AsyncClient):
    access_token = await _signup_owner_and_get_token(client)

    response = await client.get("/api/v1/users/me", headers=_auth_headers(access_token))
    assert response.status_code == 200
    assert response.json()["email"] == TEST_EMAIL
    assert response.json()["tenant"]["role"] == "owner"


@pytest.mark.asyncio
async def test_refresh_rotates_tokens(client: AsyncClient):
    assert (await _signup_owner(client)).status_code == 200
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


# --- Logout -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logout_requires_authentication(client: AsyncClient):
    response = await client.post("/api/v1/auth/logout")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_logout_returns_no_content_for_an_authenticated_user(
    client: AsyncClient,
):
    access_token = await _signup_owner_and_get_token(client)

    response = await client.post(
        "/api/v1/auth/logout", headers=_auth_headers(access_token)
    )

    assert response.status_code == 204


# --- Account deletion --------------------------------------------------------


async def _create_plain_user_and_login(
    client: AsyncClient, *, uid: str, email: str, username: str
) -> str:
    """Creates a User row directly (no tenant -- account deletion's
    happy path doesn't depend on tenant membership, only Owner-ship blocks
    it, see test_delete_account_rejects_owner_of_a_tenant) and
    signs in, returning the access token."""
    async with async_session_factory() as session:
        session.add(User(email=email, username=username))
        await session.commit()
    login_response = await _login(client, uid=uid, email=email)
    assert login_response.status_code == 200, login_response.text
    return login_response.json()["access_token"]


@pytest.mark.asyncio
async def test_delete_account_rejects_owner_of_a_tenant(client: AsyncClient):
    access_token = await _signup_owner_and_get_token(client)

    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        response = await client.request(
            "DELETE",
            "/api/v1/auth/account",
            json={"id_token": "fake"},
            headers=_auth_headers(access_token),
        )

    assert response.status_code == 409

    me = await client.get("/api/v1/users/me", headers=_auth_headers(access_token))
    assert me.status_code == 200


@pytest.mark.asyncio
async def test_delete_account_removes_user_and_calls_firebase_delete(
    client: AsyncClient,
):
    uid = "test-plain-deletable"
    email = "test.plain-deletable@example.com"
    access_token = await _create_plain_user_and_login(
        client, uid=uid, email=email, username="test-plain-deletable"
    )

    with (
        patch(
            "app.utils.firebase.verify_firebase_id_token",
            return_value=_fake_claims(uid=uid, email=email),
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
    mock_delete.assert_called_once_with(uid)

    me = await client.get("/api/v1/users/me", headers=_auth_headers(access_token))
    assert me.status_code == 401


@pytest.mark.asyncio
async def test_delete_account_rejects_stale_firebase_token(client: AsyncClient):
    access_token = await _signup_owner_and_get_token(client)

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
    access_token = await _signup_owner_and_get_token(client)

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
