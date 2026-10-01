import time
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select, update

from app.database.models import Invitation, Organization, OrganizationMember, User
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
            .values(organization_id=None)
        )
        await session.commit()
        org_result = await session.execute(
            select(Organization).where(Organization.domain.like(f"{PREFIX}%"))
        )
        for organization in org_result.scalars().all():
            await session.delete(organization)
        await session.commit()
        await session.execute(delete(User).where(User.email.like(f"{PREFIX}%")))
        await session.commit()


async def _create_org_owner_and_pending_invite(
    *, invitee_email: str, role: str = "member", suffix: str
) -> str:
    """Returns a raw invitation token. Bypasses HTTP for org/invite setup
    (owner-signup and the invite-creation endpoint are covered by their
    own tasks' tests) by writing directly to the DB and capturing the
    raw token InvitationService.create would have emailed."""
    async with async_session_factory() as session:
        owner = User(
            email=f"{PREFIX}-owner-{suffix}@{PREFIX}.example.com",
            username=f"{PREFIX}-owner-{suffix}",
        )
        session.add(owner)
        await session.flush()
        org = Organization(
            name=f"{PREFIX}-org-{suffix}",
            domain=f"{PREFIX}-{suffix}.example.com",
            owner_user_id=owner.id,
        )
        session.add(org)
        await session.flush()
        owner_membership = OrganizationMember(
            organization_id=org.id, user_id=owner.id, role="owner"
        )
        session.add(owner_membership)
        await session.commit()

        with patch("app.service.invitation_service.send_email", new=AsyncMock()):
            _invitation, raw_token = await InvitationService.create(
                session,
                organization=org,
                inviter_membership=owner_membership,
                email=invitee_email,
                role=role,
            )
        return raw_token


@pytest.mark.asyncio
async def test_accept_invitation_returns_a_token_pair(client: AsyncClient):
    raw_token = await _create_org_owner_and_pending_invite(
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
async def test_accept_invitation_rejects_mismatched_email(client: AsyncClient):
    raw_token = await _create_org_owner_and_pending_invite(
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
    protects test_organizations_router.py's `_invite_and_accept` helper
    from silently breaking if the URL shape ever changes."""
    async with async_session_factory() as session:
        owner = User(
            email=f"{PREFIX}-urlcheck-owner@{PREFIX}.example.com",
            username=f"{PREFIX}-urlcheck-owner",
        )
        session.add(owner)
        await session.flush()
        org = Organization(
            name=f"{PREFIX}-org-urlcheck",
            domain=f"{PREFIX}-urlcheck.example.com",
            owner_user_id=owner.id,
        )
        session.add(org)
        await session.flush()
        owner_membership = OrganizationMember(
            organization_id=org.id, user_id=owner.id, role="owner"
        )
        session.add(owner_membership)
        await session.commit()

        with patch(
            "app.service.invitation_service.send_email", new=AsyncMock()
        ) as mock_send:
            await InvitationService.create(
                session,
                organization=org,
                inviter_membership=owner_membership,
                email=f"{PREFIX}-urlcheck-invitee@{PREFIX}-urlcheck.example.com",
                role="member",
            )

    html_body = mock_send.call_args.kwargs["html_body"]
    accept_url = html_body.split('href="')[1].split('"')[0]
    token = parse_qs(urlparse(accept_url).query)["token"][0]
    assert token
