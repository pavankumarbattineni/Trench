import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.database.models import Credential, Invitation, Tenant, User
from app.database.session import async_session_factory


@pytest.mark.asyncio
async def test_invitation_role_defaults_to_member_and_status_defaults_to_pending():
    """There's no DB-level check constraint blocking role='owner' here
    (it's enforced in InvitationService instead), but this test documents
    the default and that the table round-trips correctly.

    Postgres enforces foreign keys immediately (not deferred), so the
    tenant_id/invited_by_user_id FKs need real rows to flush against -- a
    fake UUID raises IntegrityError, not a silent pass.
    """
    async with async_session_factory() as session:
        tenant = Tenant(
            name=f"test-invtenant-{uuid.uuid4().hex[:8]}",
            domain="test-inv.example.com",
        )
        session.add(tenant)
        await session.flush()
        owner = User(
            email="test.invowner@example.com",
            username="test-invowner",
            tenant_id=tenant.id,
            role="owner",
        )
        session.add(owner)
        await session.flush()

        invitation = Invitation(
            tenant_id=tenant.id,
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
async def test_user_tenant_fields_default_to_member_without_access():
    """A user's tenant role, company-knowledge grant, and free-tier usage
    counter all live directly on `users` now -- no join tables."""
    async with async_session_factory() as session:
        tenant = Tenant(
            name=f"test-defaults-{uuid.uuid4().hex[:8]}",
            domain="test-defaults.example.com",
        )
        session.add(tenant)
        await session.flush()
        user = User(
            email="test.defaults@example.com",
            username="test-defaults",
            tenant_id=tenant.id,
        )
        session.add(user)
        await session.flush()
        await session.refresh(user)

        assert user.role == "member"
        assert user.has_company_knowledge_access is False
        assert user.documents_uploaded_count == 0
        await session.rollback()


@pytest.mark.asyncio
async def test_users_role_rejects_invalid_value():
    async with async_session_factory() as session:
        tenant = Tenant(
            name=f"test-tenant-{uuid.uuid4().hex[:8]}",
            domain="test-ownercheck.example.com",
        )
        session.add(tenant)
        await session.flush()
        user = User(
            email="test.ownercheck@example.com",
            username="test-ownercheck",
            tenant_id=tenant.id,
            role="owner",
        )
        session.add(user)
        await session.flush()
        assert user.role == "owner"

        user.role = "superadmin"
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def _tenant_and_user(session, suffix: str) -> tuple[Tenant, User]:
    tenant = Tenant(
        name=f"test-cred-{suffix}-{uuid.uuid4().hex[:8]}",
        domain=f"test-cred-{suffix}.example.com",
    )
    session.add(tenant)
    await session.flush()
    user = User(
        email=f"test.cred-{suffix}@example.com",
        username=f"test-cred-{suffix}",
        tenant_id=tenant.id,
        role="owner",
    )
    session.add(user)
    await session.flush()
    return tenant, user


def _credential(**owner) -> Credential:
    return Credential(
        provider_type="openai_llm",
        encrypted_credential="x",
        validated_at=datetime.now(UTC),
        **owner,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_kind", ["both", "neither"])
async def test_credential_must_have_exactly_one_owner(owner_kind: str):
    async with async_session_factory() as session:
        tenant, user = await _tenant_and_user(session, f"xor-{owner_kind}")
        owner = (
            {"user_id": user.id, "tenant_id": tenant.id}
            if owner_kind == "both"
            else {}
        )
        session.add(_credential(**owner))
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["user_id", "tenant_id"])
async def test_credential_is_unique_per_owner_and_provider(scope: str):
    async with async_session_factory() as session:
        tenant, user = await _tenant_and_user(session, f"uniq-{scope.split('_')[0]}")
        owner_id = user.id if scope == "user_id" else tenant.id
        session.add(_credential(**{scope: owner_id}))
        await session.flush()
        session.add(_credential(**{scope: owner_id}))
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


@pytest.mark.asyncio
async def test_same_provider_can_exist_once_per_scope_side_by_side():
    """The partial unique indexes are per-scope: a user's personal key
    and their tenant's key for the same provider don't collide."""
    async with async_session_factory() as session:
        tenant, user = await _tenant_and_user(session, "side-by-side")
        session.add(_credential(user_id=user.id))
        session.add(_credential(tenant_id=tenant.id, set_by_user_id=user.id))
        await session.flush()
        await session.rollback()


@pytest.mark.asyncio
async def test_deleting_the_user_who_set_a_tenant_credential_keeps_the_credential():
    """set_by_user_id is ON DELETE SET NULL -- deleting whoever once set a
    tenant credential must neither be blocked by it nor delete it."""
    async with async_session_factory() as session:
        tenant, owner = await _tenant_and_user(session, "setby-owner")
        admin = User(
            email="test.cred-setby-admin@example.com",
            username="test-cred-setby-admin",
            tenant_id=tenant.id,
            role="admin",
        )
        session.add(admin)
        await session.flush()
        credential = _credential(tenant_id=tenant.id, set_by_user_id=admin.id)
        session.add(credential)
        await session.commit()

        try:
            await session.delete(admin)
            await session.commit()
            await session.refresh(credential)
            assert credential.set_by_user_id is None
            assert credential.tenant_id == tenant.id
        finally:
            owner.tenant_id = None
            await session.commit()
            await session.delete(tenant)
            await session.delete(owner)
            await session.commit()


@pytest.mark.asyncio
async def test_users_role_rejects_the_old_app_level_user_value():
    """The old Trench app role ("admin" | "user") is gone -- "user" is no
    longer a valid role at all."""
    async with async_session_factory() as session:
        session.add(
            User(
                email="test.oldrole@example.com",
                username="test-oldrole",
                role="user",
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
