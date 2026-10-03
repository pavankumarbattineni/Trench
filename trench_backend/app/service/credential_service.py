"""Business logic for storing and retrieving BYOK credentials -- both a
user's personal ones and a tenant's shared one, which live in the same
`credentials` table (see app.database.models.Credential).

Every method is scoped by exactly one of `user_id` (personal) or
`tenant_id` (tenant); passing both or neither is a programming error and
raises ValueError. Authorization (e.g. "only the tenant's Owner may set
its credential") is the router's job -- this service trusts its caller.
How a credential is *resolved* for a query (and what happens when one is
missing) is likewise not decided here -- see
LLMClientService.resolve_for_knowledge.

Nothing about a stored credential is ever exposed to the frontend beyond a
masked preview (first/last 4 characters) -- never the encrypted blob, and
never a decrypted key.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Credential
from app.service.credential_validation_service import CredentialValidationService
from app.utils.encryption import decrypt_secret, encrypt_secret

# Which BYOK credential (see VALID_PROVIDER_TYPES in app/schemas/credential.py)
# a given LLM provider (app.database.models.Provider.name) needs. The one
# provider absent here -- "groq" -- is the platform-owned free default and
# never needs a per-user credential. Single source of truth: both
# LLMClientService (generation-time resolution) and the /config + model-
# selection endpoints (UI gating, selection-time validation) import this
# rather than each hardcoding their own copy.
LLM_PROVIDER_TO_CREDENTIAL_TYPE = {
    "openai": "openai_llm",
    "anthropic": "anthropic_llm",
    "google": "gemini_llm",
}


def _mask_key(api_key: str) -> str:
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:4]}{'*' * (len(api_key) - 8)}{api_key[-4:]}"


def _owner_condition(
    user_id: uuid.UUID | None, tenant_id: uuid.UUID | None
) -> ColumnElement[bool]:
    if (user_id is None) == (tenant_id is None):
        raise ValueError("Exactly one of user_id or tenant_id must be given")
    if user_id is not None:
        return Credential.user_id == user_id
    return Credential.tenant_id == tenant_id


class CredentialService:
    @staticmethod
    async def save_credential(
        db: AsyncSession,
        *,
        provider_type: str,
        api_key: str,
        user_id: uuid.UUID | None = None,
        tenant_id: uuid.UUID | None = None,
        set_by_user_id: uuid.UUID | None = None,
    ) -> Credential:
        """Validates `api_key` against the provider, then creates or
        replaces the scope's credential for `provider_type`.

        Args:
            set_by_user_id: Who set a tenant credential (required for
                tenant scope, rejected for personal scope -- a personal
                credential is always set by its own user).

        Raises:
            ValueError: if not exactly one of user_id/tenant_id is given,
                or set_by_user_id doesn't match the scope.
        """
        condition = _owner_condition(user_id, tenant_id)
        if (tenant_id is not None) != (set_by_user_id is not None):
            raise ValueError(
                "set_by_user_id is required for, and only for, tenant scope"
            )

        await CredentialValidationService.validate(provider_type, api_key)

        result = await db.execute(
            select(Credential).where(
                condition, Credential.provider_type == provider_type
            )
        )
        credential = result.scalar_one_or_none()
        if credential is None:
            credential = Credential(
                user_id=user_id, tenant_id=tenant_id, provider_type=provider_type
            )
            db.add(credential)

        credential.encrypted_credential = encrypt_secret(api_key, purpose="byok")
        credential.set_by_user_id = set_by_user_id
        credential.validated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(credential)
        return credential

    @staticmethod
    async def list_credentials(
        db: AsyncSession,
        *,
        user_id: uuid.UUID | None = None,
        tenant_id: uuid.UUID | None = None,
    ) -> list[Credential]:
        result = await db.execute(
            select(Credential).where(_owner_condition(user_id, tenant_id))
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_credential(
        db: AsyncSession,
        *,
        provider_type: str,
        user_id: uuid.UUID | None = None,
        tenant_id: uuid.UUID | None = None,
    ) -> Credential | None:
        result = await db.execute(
            select(Credential).where(
                _owner_condition(user_id, tenant_id),
                Credential.provider_type == provider_type,
            )
        )
        return result.scalar_one_or_none()

    @classmethod
    async def get_decrypted(
        cls,
        db: AsyncSession,
        *,
        provider_type: str,
        user_id: uuid.UUID | None = None,
        tenant_id: uuid.UUID | None = None,
    ) -> str | None:
        """Returns the decrypted key, or None if the scope has none for
        this provider_type. What None *means* (a hard error for a personal
        query, a silent platform-default fallback for a company one) is
        the caller's decision -- see LLMClientService."""
        credential = await cls.get_credential(
            db, provider_type=provider_type, user_id=user_id, tenant_id=tenant_id
        )
        if credential is None:
            return None
        return decrypt_secret(credential.encrypted_credential, purpose="byok")

    @staticmethod
    async def has_credential(
        db: AsyncSession,
        *,
        provider_type: str,
        user_id: uuid.UUID | None = None,
        tenant_id: uuid.UUID | None = None,
    ) -> bool:
        result = await db.execute(
            select(Credential.id).where(
                _owner_condition(user_id, tenant_id),
                Credential.provider_type == provider_type,
            )
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def delete_credential(
        db: AsyncSession,
        *,
        provider_type: str,
        user_id: uuid.UUID | None = None,
        tenant_id: uuid.UUID | None = None,
    ) -> None:
        await db.execute(
            delete(Credential).where(
                _owner_condition(user_id, tenant_id),
                Credential.provider_type == provider_type,
            )
        )
        await db.commit()

    @staticmethod
    def masked_preview(credential: Credential) -> str:
        """Decrypts only far enough to render a masked preview (first/last
        4 characters) -- the full plaintext key never leaves this call."""
        plaintext = decrypt_secret(credential.encrypted_credential, purpose="byok")
        return _mask_key(plaintext)

    @staticmethod
    def mask_key(plaintext_api_key: str) -> str:
        """Masks an already-decrypted key -- the same masking
        `masked_preview` applies to a stored credential."""
        return _mask_key(plaintext_api_key)

    @staticmethod
    def required_credential_type(provider_name: str) -> str | None:
        """The BYOK provider_type a given LLM provider needs, or None if it
        needs no credential at all (the platform-owned Groq default)."""
        return LLM_PROVIDER_TO_CREDENTIAL_TYPE.get(provider_name)
