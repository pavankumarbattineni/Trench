"""Tenant-scoped credentials through the unified CredentialService (the
same service and `credentials` table personal credentials use -- see
test_credential_service.py for the personal side)."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select, update

from app.database.models import Credential, Tenant, User
from app.database.session import async_session_factory
from app.service.credential_service import CredentialService

PREFIX = "test-tenant-credential-service"
_VALIDATE = "app.service.credential_service.CredentialValidationService.validate"


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        await session.execute(
            update(User)
            .where(User.email.like(f"%{PREFIX}%"))
            .values(tenant_id=None)
        )
        await session.commit()
        tenant_result = await session.execute(
            select(Tenant).where(Tenant.domain.like(f"%{PREFIX}%"))
        )
        for tenant in tenant_result.scalars().all():
            await session.delete(tenant)  # cascades its credentials
        await session.commit()
        await session.execute(delete(User).where(User.email.like(f"%{PREFIX}%")))
        await session.commit()


async def _make_tenant_and_owner(session, suffix: str) -> tuple[Tenant, User]:
    tenant = Tenant(
        name=f"{PREFIX}-tenant-{suffix}",
        domain=f"{PREFIX}-{suffix}.example.com",
    )
    session.add(tenant)
    await session.flush()
    owner = User(
        email=f"owner@{PREFIX}-{suffix}.example.com",
        username=f"{PREFIX}-owner-{suffix}",
        tenant_id=tenant.id,
        role="owner",
    )
    session.add(owner)
    await session.commit()
    return tenant, owner


async def _save(session, tenant: Tenant, owner: User, api_key: str) -> Credential:
    with patch(_VALIDATE, new=AsyncMock()):
        return await CredentialService.save_credential(
            session,
            tenant_id=tenant.id,
            provider_type="openai_llm",
            api_key=api_key,
            set_by_user_id=owner.id,
        )


@pytest.mark.asyncio
async def test_save_then_get_decrypted_round_trips():
    async with async_session_factory() as session:
        tenant, owner = await _make_tenant_and_owner(session, "roundtrip")
        saved = await _save(session, tenant, owner, "sk-test-123456789")

        assert saved.tenant_id == tenant.id
        assert saved.user_id is None
        assert saved.set_by_user_id == owner.id
        decrypted = await CredentialService.get_decrypted(
            session, tenant_id=tenant.id, provider_type="openai_llm"
        )
        assert decrypted == "sk-test-123456789"


@pytest.mark.asyncio
async def test_get_decrypted_returns_none_when_unset():
    async with async_session_factory() as session:
        result = await CredentialService.get_decrypted(
            session, tenant_id=uuid.uuid4(), provider_type="openai_llm"
        )
    assert result is None


@pytest.mark.asyncio
async def test_save_overwrites_an_existing_credential_for_the_same_provider():
    async with async_session_factory() as session:
        tenant, owner = await _make_tenant_and_owner(session, "overwrite")
        await _save(session, tenant, owner, "sk-first-key-00000000")
        await _save(session, tenant, owner, "sk-second-key-11111111")

        decrypted = await CredentialService.get_decrypted(
            session, tenant_id=tenant.id, provider_type="openai_llm"
        )
        assert decrypted == "sk-second-key-11111111"
        credentials = await CredentialService.list_credentials(
            session, tenant_id=tenant.id
        )
        assert len(credentials) == 1


@pytest.mark.asyncio
async def test_delete_removes_the_credential():
    async with async_session_factory() as session:
        tenant, owner = await _make_tenant_and_owner(session, "delete")
        await _save(session, tenant, owner, "sk-test-123456789")

        await CredentialService.delete_credential(
            session, tenant_id=tenant.id, provider_type="openai_llm"
        )

        decrypted = await CredentialService.get_decrypted(
            session, tenant_id=tenant.id, provider_type="openai_llm"
        )
        assert decrypted is None


@pytest.mark.asyncio
async def test_tenant_and_personal_scopes_never_see_each_other():
    """The Owner's personal key and the tenant's key for the same provider
    are separate rows -- reading one scope never returns the other."""
    async with async_session_factory() as session:
        tenant, owner = await _make_tenant_and_owner(session, "isolation")
        await _save(session, tenant, owner, "sk-tenant-key-0000")
        with patch(_VALIDATE, new=AsyncMock()):
            await CredentialService.save_credential(
                session,
                user_id=owner.id,
                provider_type="openai_llm",
                api_key="sk-personal-key-1111",
            )

        assert (
            await CredentialService.get_decrypted(
                session, tenant_id=tenant.id, provider_type="openai_llm"
            )
            == "sk-tenant-key-0000"
        )
        assert (
            await CredentialService.get_decrypted(
                session, user_id=owner.id, provider_type="openai_llm"
            )
            == "sk-personal-key-1111"
        )

        await CredentialService.delete_credential(
            session, tenant_id=tenant.id, provider_type="openai_llm"
        )
        assert await CredentialService.has_credential(
            session, user_id=owner.id, provider_type="openai_llm"
        )
        assert not await CredentialService.has_credential(
            session, tenant_id=tenant.id, provider_type="openai_llm"
        )


@pytest.mark.asyncio
async def test_saving_a_tenant_credential_requires_set_by_user_id():
    async with async_session_factory() as session:
        tenant, _owner = await _make_tenant_and_owner(session, "setby-required")
        with patch(_VALIDATE, new=AsyncMock()), pytest.raises(ValueError):
            await CredentialService.save_credential(
                session,
                tenant_id=tenant.id,
                provider_type="openai_llm",
                api_key="sk-test-123456789",
            )
