import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select, update

from app.database.models import Invitation, Tenant, User
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
        # users.tenant_id has no ondelete -- detach users before deleting
        # their tenant.
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


async def _make_tenant_and_owner(session, suffix: str) -> tuple[Tenant, User]:
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
    await session.flush()
    return tenant, owner


@pytest.mark.asyncio
async def test_create_sends_an_email_and_stores_only_the_token_hash():
    from app.service.invitation_service import InvitationService

    async with async_session_factory() as session:
        org, owner = await _make_tenant_and_owner(session, "create")
        await session.commit()

        with patch(
            "app.service.invitation_service.send_email", new=AsyncMock()
        ) as mock_send:
            invitation, raw_token = await InvitationService.create(
                session,
                tenant=org,
                inviter=owner,
                email=f"{PREFIX}-invitee-create@{org.domain}",
                role="member",
            )

        assert mock_send.await_count == 1
        assert invitation.token_hash == hashlib.sha256(raw_token.encode()).hexdigest()
        assert invitation.status == "pending"
        assert invitation.role == "member"


@pytest.mark.asyncio
async def test_create_builds_the_accept_link_from_frontend_base_url():
    """The emailed link must point at the configured frontend, not a
    hardcoded placeholder domain -- see app.config.TrenchConfig
    .FRONTEND_BASE_URL."""
    from app.service.invitation_service import InvitationService

    async with async_session_factory() as session:
        org, owner = await _make_tenant_and_owner(session, "accept-link")
        await session.commit()

        with patch(
            "app.service.invitation_service.send_email", new=AsyncMock()
        ) as mock_send:
            _invitation, raw_token = await InvitationService.create(
                session,
                tenant=org,
                inviter=owner,
                email=f"{PREFIX}-invitee-accept-link@{org.domain}",
                role="member",
            )

        html_body = mock_send.await_args.kwargs["html_body"]
        accept_url = html_body.split('href="')[1].split('"')[0]
        assert accept_url == f"http://localhost:3000/invite/accept?token={raw_token}"


@pytest.mark.asyncio
async def test_create_rejects_an_email_with_a_different_domain():
    from app.service.invitation_service import DomainMismatchError, InvitationService

    async with async_session_factory() as session:
        org, owner = await _make_tenant_and_owner(session, "domain-mismatch")
        await session.commit()

        with patch("app.service.invitation_service.send_email", new=AsyncMock()):
            with pytest.raises(DomainMismatchError):
                await InvitationService.create(
                    session,
                    tenant=org,
                    inviter=owner,
                    email=f"{PREFIX}-outsider@not-{org.domain}",
                    role="member",
                )


@pytest.mark.asyncio
async def test_admin_cannot_create_an_admin_role_invitation():
    from app.service.invitation_service import InvitationService, TooManyRoleError

    async with async_session_factory() as session:
        org, _owner = await _make_tenant_and_owner(session, "admin-limit")
        await session.commit()

        admin_user = User(
            email=f"{PREFIX}-admin-admin-limit@{PREFIX}.example.com",
            username=f"{PREFIX}-admin-admin-limit",
            tenant_id=org.id,
            role="admin",
        )
        session.add(admin_user)
        await session.commit()

        with patch("app.service.invitation_service.send_email", new=AsyncMock()):
            with pytest.raises(TooManyRoleError):
                await InvitationService.create(
                    session,
                    tenant=org,
                    inviter=admin_user,
                    email=f"{PREFIX}-wannabe-admin@{PREFIX}.example.com",
                    role="admin",
                )


@pytest.mark.asyncio
async def test_accept_rejects_a_mismatched_email():
    from app.service.invitation_service import InvitationService

    async with async_session_factory() as session:
        org, owner = await _make_tenant_and_owner(session, "mismatch")
        await session.commit()
        with patch("app.service.invitation_service.send_email", new=AsyncMock()):
            _invitation, raw_token = await InvitationService.create(
                session,
                tenant=org,
                inviter=owner,
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
        org, owner = await _make_tenant_and_owner(session, "accept-default")
        await session.commit()
        email = f"{PREFIX}-invitee-accept-default@{org.domain}"
        with patch("app.service.invitation_service.send_email", new=AsyncMock()):
            _invitation, raw_token = await InvitationService.create(
                session,
                tenant=org,
                inviter=owner,
                email=email,
                role="member",
            )

        accepted_invitation, user, created = await InvitationService.accept(
            session, raw_token=raw_token, authenticated_email=email
        )

        assert created is True
        assert accepted_invitation.status == "accepted"
        assert user.tenant_id == org.id
        assert user.role == "member"
        assert user.has_company_knowledge_access is True
        assert KnowledgeAccessService.has_company_access(user) is True
        assert (
            await KnowledgeAccessService.authorized_company_tenant_id(
                session, user_id=user.id
            )
            == org.id
        )


@pytest.mark.asyncio
async def test_accept_as_admin_sets_role_without_a_grant():
    """Admins' company access is role-derived -- accepting an admin
    invitation sets role="admin" directly and needs no grant flag."""
    from app.service.invitation_service import InvitationService
    from app.service.knowledge_access_service import KnowledgeAccessService

    async with async_session_factory() as session:
        org, owner = await _make_tenant_and_owner(session, "accept-admin")
        await session.commit()
        email = f"{PREFIX}-invitee-accept-admin@{org.domain}"
        with patch("app.service.invitation_service.send_email", new=AsyncMock()):
            _invitation, raw_token = await InvitationService.create(
                session, tenant=org, inviter=owner, email=email, role="admin"
            )

        _accepted, user, _created = await InvitationService.accept(
            session, raw_token=raw_token, authenticated_email=email
        )

        assert user.tenant_id == org.id
        assert user.role == "admin"
        assert user.has_company_knowledge_access is False
        assert KnowledgeAccessService.has_company_access(user) is True


@pytest.mark.asyncio
async def test_accept_rejects_an_already_accepted_token():
    from app.service.invitation_service import InvitationService

    async with async_session_factory() as session:
        org, owner = await _make_tenant_and_owner(session, "reuse")
        await session.commit()
        email = f"{PREFIX}-invitee-reuse@{org.domain}"
        with patch("app.service.invitation_service.send_email", new=AsyncMock()):
            _invitation, raw_token = await InvitationService.create(
                session,
                tenant=org,
                inviter=owner,
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
        org, owner = await _make_tenant_and_owner(session, "expired")
        await session.commit()
        email = f"{PREFIX}-invitee-expired@{org.domain}"
        with patch("app.service.invitation_service.send_email", new=AsyncMock()):
            invitation, raw_token = await InvitationService.create(
                session,
                tenant=org,
                inviter=owner,
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
        org, owner = await _make_tenant_and_owner(session, "resend")
        await session.commit()
        email = f"{PREFIX}-invitee-resend@{org.domain}"
        with patch("app.service.invitation_service.send_email", new=AsyncMock()):
            invitation, first_token = await InvitationService.create(
                session,
                tenant=org,
                inviter=owner,
                email=email,
                role="member",
            )

        with patch(
            "app.service.invitation_service.send_email", new=AsyncMock()
        ) as mock_send:
            new_token = await InvitationService.resend(
                session, invitation=invitation, tenant=org
            )

        assert mock_send.await_count == 1
        assert new_token != first_token
        assert invitation.token_hash == hashlib.sha256(new_token.encode()).hexdigest()


@pytest.mark.asyncio
async def test_revoke_sets_status_to_revoked():
    from app.service.invitation_service import InvitationService

    async with async_session_factory() as session:
        org, owner = await _make_tenant_and_owner(session, "revoke")
        await session.commit()
        with patch("app.service.invitation_service.send_email", new=AsyncMock()):
            invitation, _raw_token = await InvitationService.create(
                session,
                tenant=org,
                inviter=owner,
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
    dedupes/rotates against an existing pending row by (tenant, email)."""
    from app.service.invitation_service import InvitationService

    async with async_session_factory() as session:
        org, owner = await _make_tenant_and_owner(session, "delivery-fail")
        await session.commit()
        email = f"{PREFIX}-invitee-delivery-fail@{org.domain}"

        with patch(
            "app.service.invitation_service.send_email",
            new=AsyncMock(side_effect=OSError("smtp unreachable")),
        ):
            with pytest.raises(InvitationService.EmailDeliveryError):
                await InvitationService.create(
                    session,
                    tenant=org,
                    inviter=owner,
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
        org, owner = await _make_tenant_and_owner(session, "resend-fail")
        await session.commit()
        with patch("app.service.invitation_service.send_email", new=AsyncMock()):
            invitation, _raw_token = await InvitationService.create(
                session,
                tenant=org,
                inviter=owner,
                email=f"{PREFIX}-invitee-resend-fail@{org.domain}",
                role="member",
            )

        with patch(
            "app.service.invitation_service.send_email",
            new=AsyncMock(side_effect=OSError("smtp unreachable")),
        ):
            with pytest.raises(InvitationService.EmailDeliveryError):
                await InvitationService.resend(
                    session, invitation=invitation, tenant=org
                )
