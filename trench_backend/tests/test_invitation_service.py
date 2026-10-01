import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select, update

from app.database.models import Invitation, Organization, OrganizationMember, User
from app.database.session import async_session_factory

PREFIX = "test-invitation-service"


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        await session.execute(
            delete(Invitation).where(Invitation.email.like(f"{PREFIX}%"))
        )
        await session.commit()
        # Break the circular FK (users.organization_id <-> organizations.owner_user_id)
        # before deleting either side.
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


async def _make_org_and_owner(
    session, suffix: str
) -> tuple[Organization, OrganizationMember]:
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
    membership = OrganizationMember(
        organization_id=org.id, user_id=owner.id, role="owner"
    )
    session.add(membership)
    await session.flush()
    return org, membership


@pytest.mark.asyncio
async def test_create_sends_an_email_and_stores_only_the_token_hash():
    from app.service.invitation_service import InvitationService

    async with async_session_factory() as session:
        org, owner_membership = await _make_org_and_owner(session, "create")
        await session.commit()

        with patch(
            "app.service.invitation_service.send_email", new=AsyncMock()
        ) as mock_send:
            invitation, raw_token = await InvitationService.create(
                session,
                organization=org,
                inviter_membership=owner_membership,
                email=f"{PREFIX}-invitee-create@{org.domain}",
                role="member",
            )

        assert mock_send.await_count == 1
        assert invitation.token_hash == hashlib.sha256(raw_token.encode()).hexdigest()
        assert invitation.status == "pending"
        assert invitation.role == "member"


@pytest.mark.asyncio
async def test_create_rejects_an_email_with_a_different_domain():
    from app.service.invitation_service import DomainMismatchError, InvitationService

    async with async_session_factory() as session:
        org, owner_membership = await _make_org_and_owner(session, "domain-mismatch")
        await session.commit()

        with patch("app.service.invitation_service.send_email", new=AsyncMock()):
            with pytest.raises(DomainMismatchError):
                await InvitationService.create(
                    session,
                    organization=org,
                    inviter_membership=owner_membership,
                    email=f"{PREFIX}-outsider@not-{org.domain}",
                    role="member",
                )


@pytest.mark.asyncio
async def test_admin_cannot_create_an_admin_role_invitation():
    from app.service.invitation_service import InvitationService, TooManyRoleError

    async with async_session_factory() as session:
        org, _owner_membership = await _make_org_and_owner(session, "admin-limit")
        await session.commit()

        admin_user = User(
            email=f"{PREFIX}-admin-admin-limit@{PREFIX}.example.com",
            username=f"{PREFIX}-admin-admin-limit",
        )
        session.add(admin_user)
        await session.flush()
        admin_membership = OrganizationMember(
            organization_id=org.id, user_id=admin_user.id, role="admin"
        )
        session.add(admin_membership)
        await session.commit()

        with patch("app.service.invitation_service.send_email", new=AsyncMock()):
            with pytest.raises(TooManyRoleError):
                await InvitationService.create(
                    session,
                    organization=org,
                    inviter_membership=admin_membership,
                    email=f"{PREFIX}-wannabe-admin@{PREFIX}.example.com",
                    role="admin",
                )


@pytest.mark.asyncio
async def test_accept_rejects_a_mismatched_email():
    from app.service.invitation_service import InvitationService

    async with async_session_factory() as session:
        org, owner_membership = await _make_org_and_owner(session, "mismatch")
        await session.commit()
        with patch("app.service.invitation_service.send_email", new=AsyncMock()):
            _invitation, raw_token = await InvitationService.create(
                session,
                organization=org,
                inviter_membership=owner_membership,
                email=f"{PREFIX}-invitee-mismatch@{org.domain}",
                role="member",
            )

        with pytest.raises(InvitationService.EmailMismatchError):
            await InvitationService.accept(
                session,
                raw_token=raw_token,
                authenticated_email=f"{PREFIX}-someone-else@{org.domain}",
            )


@pytest.mark.asyncio
async def test_accept_creates_membership_and_grants_default_knowledge_access():
    from app.service.invitation_service import InvitationService
    from app.service.knowledge_access_service import KnowledgeAccessService

    async with async_session_factory() as session:
        org, owner_membership = await _make_org_and_owner(session, "accept-default")
        await session.commit()
        email = f"{PREFIX}-invitee-accept-default@{org.domain}"
        with patch("app.service.invitation_service.send_email", new=AsyncMock()):
            _invitation, raw_token = await InvitationService.create(
                session,
                organization=org,
                inviter_membership=owner_membership,
                email=email,
                role="member",
            )

        accepted_invitation, user, created = await InvitationService.accept(
            session, raw_token=raw_token, authenticated_email=email
        )

        assert created is True
        assert accepted_invitation.status == "accepted"
        assert user.organization_id == org.id
        has_access = await KnowledgeAccessService.has_company_access(
            session, user_id=user.id, organization_id=org.id
        )
        assert has_access is True


@pytest.mark.asyncio
async def test_accept_rejects_an_already_accepted_token():
    from app.service.invitation_service import InvitationService

    async with async_session_factory() as session:
        org, owner_membership = await _make_org_and_owner(session, "reuse")
        await session.commit()
        email = f"{PREFIX}-invitee-reuse@{org.domain}"
        with patch("app.service.invitation_service.send_email", new=AsyncMock()):
            _invitation, raw_token = await InvitationService.create(
                session,
                organization=org,
                inviter_membership=owner_membership,
                email=email,
                role="member",
            )
        await InvitationService.accept(
            session, raw_token=raw_token, authenticated_email=email
        )

        with pytest.raises(InvitationService.InvalidTokenError):
            await InvitationService.accept(
                session, raw_token=raw_token, authenticated_email=email
            )


@pytest.mark.asyncio
async def test_accept_rejects_an_expired_token():
    from app.service.invitation_service import InvitationService

    async with async_session_factory() as session:
        org, owner_membership = await _make_org_and_owner(session, "expired")
        await session.commit()
        email = f"{PREFIX}-invitee-expired@{org.domain}"
        with patch("app.service.invitation_service.send_email", new=AsyncMock()):
            invitation, raw_token = await InvitationService.create(
                session,
                organization=org,
                inviter_membership=owner_membership,
                email=email,
                role="member",
            )
        invitation.expires_at = datetime.now(UTC) - timedelta(days=1)
        await session.commit()

        with pytest.raises(InvitationService.InvalidTokenError):
            await InvitationService.accept(
                session, raw_token=raw_token, authenticated_email=email
            )


@pytest.mark.asyncio
async def test_resend_rotates_token_and_resends_email():
    from app.service.invitation_service import InvitationService

    async with async_session_factory() as session:
        org, owner_membership = await _make_org_and_owner(session, "resend")
        await session.commit()
        email = f"{PREFIX}-invitee-resend@{org.domain}"
        with patch("app.service.invitation_service.send_email", new=AsyncMock()):
            invitation, first_token = await InvitationService.create(
                session,
                organization=org,
                inviter_membership=owner_membership,
                email=email,
                role="member",
            )

        with patch(
            "app.service.invitation_service.send_email", new=AsyncMock()
        ) as mock_send:
            new_token = await InvitationService.resend(
                session, invitation=invitation, organization=org
            )

        assert mock_send.await_count == 1
        assert new_token != first_token
        assert invitation.token_hash == hashlib.sha256(new_token.encode()).hexdigest()


@pytest.mark.asyncio
async def test_revoke_sets_status_to_revoked():
    from app.service.invitation_service import InvitationService

    async with async_session_factory() as session:
        org, owner_membership = await _make_org_and_owner(session, "revoke")
        await session.commit()
        with patch("app.service.invitation_service.send_email", new=AsyncMock()):
            invitation, _raw_token = await InvitationService.create(
                session,
                organization=org,
                inviter_membership=owner_membership,
                email=f"{PREFIX}-invitee-revoke@{org.domain}",
                role="member",
            )

        await InvitationService.revoke(session, invitation=invitation)
        assert invitation.status == "revoked"


@pytest.mark.asyncio
async def test_create_raises_email_delivery_error_but_keeps_the_invitation_row():
    """If the SMTP send fails, the caller must be told clearly (not a bare
    exception bubbling up as a 500) -- but the already-committed Invitation
    row stays in place, since a Resend is exactly how the Owner recovers
    from a transient delivery failure, and create() itself already
    dedupes/rotates against an existing pending row by (org, email)."""
    from app.service.invitation_service import InvitationService

    async with async_session_factory() as session:
        org, owner_membership = await _make_org_and_owner(session, "delivery-fail")
        await session.commit()
        email = f"{PREFIX}-invitee-delivery-fail@{org.domain}"

        with patch(
            "app.service.invitation_service.send_email",
            new=AsyncMock(side_effect=OSError("smtp unreachable")),
        ):
            with pytest.raises(InvitationService.EmailDeliveryError):
                await InvitationService.create(
                    session,
                    organization=org,
                    inviter_membership=owner_membership,
                    email=email,
                    role="member",
                )

        result = await session.execute(
            select(Invitation).where(Invitation.email == email)
        )
        invitation = result.scalar_one()
        assert invitation.status == "pending"


@pytest.mark.asyncio
async def test_resend_raises_email_delivery_error_on_smtp_failure():
    from app.service.invitation_service import InvitationService

    async with async_session_factory() as session:
        org, owner_membership = await _make_org_and_owner(session, "resend-fail")
        await session.commit()
        with patch("app.service.invitation_service.send_email", new=AsyncMock()):
            invitation, _raw_token = await InvitationService.create(
                session,
                organization=org,
                inviter_membership=owner_membership,
                email=f"{PREFIX}-invitee-resend-fail@{org.domain}",
                role="member",
            )

        with patch(
            "app.service.invitation_service.send_email",
            new=AsyncMock(side_effect=OSError("smtp unreachable")),
        ):
            with pytest.raises(InvitationService.EmailDeliveryError):
                await InvitationService.resend(
                    session, invitation=invitation, organization=org
                )
