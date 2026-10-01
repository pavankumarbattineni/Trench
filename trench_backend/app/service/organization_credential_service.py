"""Business logic for an organization's shared BYOK credential -- the
Owner's own key, used on behalf of every member for company-knowledge
queries (see LLMClientService.resolve_for_knowledge). Mirrors
CredentialService (per-user) closely but is scoped to an organization
and settable only by its Owner (enforced by the router dependency, not
here -- this service trusts its caller the same way CredentialService
trusts its router).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import OrganizationCredential
from app.service.credential_validation_service import CredentialValidationService
from app.utils.encryption import decrypt_secret, encrypt_secret


class OrganizationCredentialService:
    @staticmethod
    async def save(
        db: AsyncSession,
        *,
        organization_id: uuid.UUID,
        provider_type: str,
        api_key: str,
        set_by_user_id: uuid.UUID,
    ) -> OrganizationCredential:
        await CredentialValidationService.validate(provider_type, api_key)

        result = await db.execute(
            select(OrganizationCredential).where(
                OrganizationCredential.organization_id == organization_id,
                OrganizationCredential.provider_type == provider_type,
            )
        )
        credential = result.scalar_one_or_none()
        if credential is None:
            credential = OrganizationCredential(
                organization_id=organization_id, provider_type=provider_type
            )
            db.add(credential)

        credential.encrypted_credential = encrypt_secret(api_key, purpose="byok")
        credential.set_by_user_id = set_by_user_id
        credential.validated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(credential)
        return credential

    @staticmethod
    async def list_for_organization(
        db: AsyncSession, *, organization_id: uuid.UUID
    ) -> list[OrganizationCredential]:
        result = await db.execute(
            select(OrganizationCredential).where(
                OrganizationCredential.organization_id == organization_id
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def delete(
        db: AsyncSession, *, organization_id: uuid.UUID, provider_type: str
    ) -> None:
        await db.execute(
            delete(OrganizationCredential).where(
                OrganizationCredential.organization_id == organization_id,
                OrganizationCredential.provider_type == provider_type,
            )
        )
        await db.commit()

    @staticmethod
    async def get_decrypted(
        db: AsyncSession, *, organization_id: uuid.UUID, provider_type: str
    ) -> str | None:
        """Returns the decrypted key, or None if the Owner hasn't set one
        for this provider_type -- callers (LLMClientService) fall back to
        the platform default in that case."""
        result = await db.execute(
            select(OrganizationCredential).where(
                OrganizationCredential.organization_id == organization_id,
                OrganizationCredential.provider_type == provider_type,
            )
        )
        credential = result.scalar_one_or_none()
        if credential is None:
            return None
        return decrypt_secret(credential.encrypted_credential, purpose="byok")
