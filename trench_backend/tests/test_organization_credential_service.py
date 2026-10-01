import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select, update

from app.database.models import Organization, OrganizationCredential, User
from app.database.session import async_session_factory
from app.service.organization_credential_service import OrganizationCredentialService

PREFIX = "test-org-credential-service"


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        await session.execute(delete(OrganizationCredential))
        await session.execute(
            update(User)
            .where(User.email.like(f"%{PREFIX}%"))
            .values(organization_id=None)
        )
        await session.commit()
        org_result = await session.execute(
            select(Organization).where(Organization.domain.like(f"%{PREFIX}%"))
        )
        for organization in org_result.scalars().all():
            await session.delete(organization)
        await session.commit()
        await session.execute(delete(User).where(User.email.like(f"%{PREFIX}%")))
        await session.commit()


async def _make_org_and_owner(session, suffix: str) -> tuple[Organization, User]:
    owner = User(
        email=f"owner@{PREFIX}-{suffix}.example.com",
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
    await session.commit()
    return org, owner


@pytest.mark.asyncio
async def test_save_then_get_decrypted_round_trips():
    async with async_session_factory() as session:
        org, owner = await _make_org_and_owner(session, "roundtrip")

        with patch(
            "app.service.organization_credential_service.CredentialValidationService.validate",
            new=AsyncMock(),
        ):
            await OrganizationCredentialService.save(
                session,
                organization_id=org.id,
                provider_type="openai_llm",
                api_key="sk-test-123456789",
                set_by_user_id=owner.id,
            )

        decrypted = await OrganizationCredentialService.get_decrypted(
            session, organization_id=org.id, provider_type="openai_llm"
        )
        assert decrypted == "sk-test-123456789"


@pytest.mark.asyncio
async def test_get_decrypted_returns_none_when_unset():
    async with async_session_factory() as session:
        result = await OrganizationCredentialService.get_decrypted(
            session, organization_id=uuid.uuid4(), provider_type="openai_llm"
        )
    assert result is None


@pytest.mark.asyncio
async def test_save_overwrites_an_existing_credential_for_the_same_provider():
    async with async_session_factory() as session:
        org, owner = await _make_org_and_owner(session, "overwrite")

        with patch(
            "app.service.organization_credential_service.CredentialValidationService.validate",
            new=AsyncMock(),
        ):
            await OrganizationCredentialService.save(
                session,
                organization_id=org.id,
                provider_type="openai_llm",
                api_key="sk-first-key-00000000",
                set_by_user_id=owner.id,
            )
            await OrganizationCredentialService.save(
                session,
                organization_id=org.id,
                provider_type="openai_llm",
                api_key="sk-second-key-11111111",
                set_by_user_id=owner.id,
            )

        decrypted = await OrganizationCredentialService.get_decrypted(
            session, organization_id=org.id, provider_type="openai_llm"
        )
        assert decrypted == "sk-second-key-11111111"
        credentials = await OrganizationCredentialService.list_for_organization(
            session, organization_id=org.id
        )
        assert len(credentials) == 1


@pytest.mark.asyncio
async def test_delete_removes_the_credential():
    async with async_session_factory() as session:
        org, owner = await _make_org_and_owner(session, "delete")

        with patch(
            "app.service.organization_credential_service.CredentialValidationService.validate",
            new=AsyncMock(),
        ):
            await OrganizationCredentialService.save(
                session,
                organization_id=org.id,
                provider_type="openai_llm",
                api_key="sk-test-123456789",
                set_by_user_id=owner.id,
            )

        await OrganizationCredentialService.delete(
            session, organization_id=org.id, provider_type="openai_llm"
        )

        decrypted = await OrganizationCredentialService.get_decrypted(
            session, organization_id=org.id, provider_type="openai_llm"
        )
        assert decrypted is None
