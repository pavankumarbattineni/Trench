import time
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select, update

from app.database.models import Invitation, Tenant, User
from app.database.session import async_session_factory
from app.service.invitation_service import InvitationService

PREFIX = "test-invite-accept"


def _fake_claims(email: str) -> dict:
    return {"uid": f"uid-{email}", "email": email, "iat": int(time.time())}


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        await session.execute(
            delete(Invitation).where(Invitation.email.like(f"{PREFIX}%"))
        )
        await session.commit()
        await session.execute(
            update(User)
            .where(User.email.like(f"{PREFIX}%"))
            .values(tenant_id=None)
        )
        await session.commit()
        tenant_result = await session.execute(
            select(Tenant).where(Tenant.domain.like(f"{PREFIX}%"))
        )
        for tenant in tenant_result.scalars().all():
            await session.delete(tenant)
        await session.commit()
        await session.execute(delete(User).where(User.email.like(f"{PREFIX}%")))
        await session.commit()


async def _create_tenant_with_owner(session, *, suffix: str) -> tuple[Tenant, User]:
    tenant = Tenant(
        name=f"{PREFIX}-tenant-{suffix}",
        domain=f"{PREFIX}-{suffix}.example.com",
    )
    session.add(tenant)
    await session.flush()
    owner = User(
        email=f"{PREFIX}-owner-{suffix}@{PREFIX}.example.com",
        username=f"{PREFIX}-owner-{suffix}",
        tenant_id=tenant.id,
        role="owner",
    )
    session.add(owner)
    await session.commit()
    return tenant, owner


async def _create_tenant_owner_and_pending_invite(
    *, invitee_email: str, role: str = "member", suffix: str
) -> str:
    """Returns a raw invitation token. Bypasses HTTP for tenant/invite
    setup (owner-signup and the invite-creation endpoint are covered by
    their own tests) by writing directly to the DB and capturing the raw
    token InvitationService.create would have emailed."""
    async with async_session_factory() as session:
        tenant, owner = await _create_tenant_with_owner(session, suffix=suffix)

        with patch("app.service.invitation_service.send_email", new=AsyncMock()):
            _invitation, raw_token = await InvitationService.create(
                session,
                tenant=tenant,
                inviter=owner,
                email=invitee_email,
                role=role,
            )
        return raw_token


@pytest.mark.asyncio
async def test_accept_invitation_returns_a_token_pair(client: AsyncClient):
    raw_token = await _create_tenant_owner_and_pending_invite(
        invitee_email=f"{PREFIX}-newhire@{PREFIX}-returns-pair.example.com",
        suffix="returns-pair",
    )

    with patch(
        "app.utils.firebase.verify_firebase_id_token",
        return_value=_fake_claims(f"{PREFIX}-newhire@{PREFIX}-returns-pair.example.com"),
    ):
        response = await client.post(
            "/api/v1/auth/invitations/accept",
            json={
                "token": raw_token,
                "id_token": "fake",
                "username": f"{PREFIX}-newhire",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]


@pytest.mark.asyncio
async def test_accept_invitation_rejects_an_invitee_already_in_a_tenant(
    client: AsyncClient,
):
    """A user belongs to at most one tenant -- accepting an invitation
    must never silently move an existing member (here, another tenant's
    Owner) out of theirs."""
    async with async_session_factory() as session:
        _tenant, existing_owner = await _create_tenant_with_owner(
            session, suffix="already"
        )
    invitee_email = existing_owner.email
    placeholder_email = f"{PREFIX}-placeholder@{PREFIX}-already-inviter.example.com"
    raw_token = await _create_tenant_owner_and_pending_invite(
        invitee_email=placeholder_email, suffix="already-inviter"
    )
    # create() only accepts emails on the inviting tenant's own domain, so
    # repoint the invitation at the existing Owner's address directly.
    async with async_session_factory() as session:
        await session.execute(
            update(Invitation)
            .where(Invitation.email == placeholder_email)
            .values(email=invitee_email)
        )
        await session.commit()

    with patch(
        "app.utils.firebase.verify_firebase_id_token",
        return_value=_fake_claims(invitee_email),
    ):
        response = await client.post(
            "/api/v1/auth/invitations/accept",
            json={"token": raw_token, "id_token": "fake"},
        )

    assert response.status_code == 409
    async with async_session_factory() as session:
        owner = (
            await session.execute(select(User).where(User.email == invitee_email))
        ).scalar_one()
        assert owner.role == "owner"
        assert owner.tenant_id is not None


@pytest.mark.asyncio
async def test_accept_invitation_rejects_mismatched_email(client: AsyncClient):
    raw_token = await _create_tenant_owner_and_pending_invite(
        invitee_email=f"{PREFIX}-newhire2@{PREFIX}-mismatch.example.com",
        suffix="mismatch",
    )

    with patch(
        "app.utils.firebase.verify_firebase_id_token",
        return_value=_fake_claims(f"{PREFIX}-wrong-person@{PREFIX}.example.com"),
    ):
        response = await client.post(
            "/api/v1/auth/invitations/accept",
            json={"token": raw_token, "id_token": "fake"},
        )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_accept_invitation_rejects_an_unknown_token(client: AsyncClient):
    with patch(
        "app.utils.firebase.verify_firebase_id_token",
        return_value=_fake_claims(f"{PREFIX}-whoever@{PREFIX}.example.com"),
    ):
        response = await client.post(
            "/api/v1/auth/invitations/accept",
            json={"token": "not-a-real-token", "id_token": "fake"},
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_accept_invitation_reuses_html_body_url_shape(client: AsyncClient):
    """Sanity check that the html_body accept_url format the helper parses
    in other test files actually matches what InvitationService emits --
    protects test_tenants_router.py's `_invite_and_accept` helper
    from silently breaking if the URL shape ever changes."""
    async with async_session_factory() as session:
        tenant, owner = await _create_tenant_with_owner(session, suffix="urlcheck")

        with patch(
            "app.service.invitation_service.send_email", new=AsyncMock()
        ) as mock_send:
            await InvitationService.create(
                session,
                tenant=tenant,
                inviter=owner,
                email=f"{PREFIX}-urlcheck-invitee@{PREFIX}-urlcheck.example.com",
                role="member",
            )

    html_body = mock_send.call_args.kwargs["html_body"]
    accept_url = html_body.split('href="')[1].split('"')[0]
    token = parse_qs(urlparse(accept_url).query)["token"][0]
    assert token
