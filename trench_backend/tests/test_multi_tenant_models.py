import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.database.models import Invitation, Organization, User
from app.database.session import async_session_factory


@pytest.mark.asyncio
async def test_invitation_role_defaults_to_member_and_status_defaults_to_pending():
    """There's no DB-level check constraint blocking role='owner' here
    (it's enforced in InvitationService instead -- see Task 4), but this
    test documents the default and that the table round-trips correctly.

    Postgres enforces foreign keys immediately (not deferred), so the
    organization_id/invited_by_user_id FKs need real rows to flush
    against -- a fake UUID raises IntegrityError, not a silent pass.
    """
    async with async_session_factory() as session:
        owner = User(email="test.invowner@example.com", username="test-invowner")
        session.add(owner)
        await session.flush()
        org = Organization(
            name=f"test-invorg-{uuid.uuid4().hex[:8]}",
            domain="test-inv.example.com",
            owner_user_id=owner.id,
        )
        session.add(org)
        await session.flush()

        invitation = Invitation(
            organization_id=org.id,
            email="test.invitee@example.com",
            token_hash="a" * 64,
            invited_by_user_id=owner.id,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        session.add(invitation)
        await session.flush()
        assert invitation.role == "member"
        assert invitation.status == "pending"
        await session.rollback()


@pytest.mark.asyncio
async def test_organization_members_role_rejects_invalid_value():
    from sqlalchemy.exc import IntegrityError

    from app.database.models import Organization, OrganizationMember, User

    async with async_session_factory() as session:
        user = User(
            email="test.ownercheck@example.com",
            username="test-ownercheck",
        )
        session.add(user)
        await session.flush()
        org = Organization(
            name=f"test-org-{uuid.uuid4().hex[:8]}",
            domain="test-ownercheck.example.com",
            owner_user_id=user.id,
        )
        session.add(org)
        await session.flush()
        member = OrganizationMember(
            organization_id=org.id, user_id=user.id, role="owner"
        )
        session.add(member)
        await session.flush()
        assert member.role == "owner"

        member.role = "superadmin"
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
