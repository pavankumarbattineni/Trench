"""Business logic for storing and retrieving a user's BYOK credentials.

Nothing about a stored credential is ever exposed to the frontend beyond a
masked preview (first/last 4 characters) -- never the encrypted blob, and
never a decrypted key.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import UserCredential
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


class CredentialService:
    @staticmethod
    async def save_credential(
        db: AsyncSession, *, user_id: uuid.UUID, provider_type: str, api_key: str
    ) -> UserCredential:
        await CredentialValidationService.validate(provider_type, api_key)

        result = await db.execute(
            select(UserCredential).where(
                UserCredential.user_id == user_id,
                UserCredential.provider_type == provider_type,
            )
        )
        credential = result.scalar_one_or_none()
        if credential is None:
            credential = UserCredential(user_id=user_id, provider_type=provider_type)
            db.add(credential)

        credential.encrypted_credential = encrypt_secret(api_key, purpose="byok")
        credential.validated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(credential)
        return credential

    @staticmethod
    async def list_credentials(
        db: AsyncSession, *, user_id: uuid.UUID
    ) -> list[UserCredential]:
        result = await db.execute(
            select(UserCredential).where(UserCredential.user_id == user_id)
        )
        return list(result.scalars().all())

    @staticmethod
    def masked_preview(credential: UserCredential) -> str:
        """Decrypts only far enough to render a masked preview (first/last
        4 characters) -- the full plaintext key never leaves this call."""
        plaintext = decrypt_secret(credential.encrypted_credential, purpose="byok")
        return _mask_key(plaintext)

    @staticmethod
    def required_credential_type(provider_name: str) -> str | None:
        """The BYOK provider_type a given LLM provider needs, or None if it
        needs no credential at all (the platform-owned Groq default)."""
        return LLM_PROVIDER_TO_CREDENTIAL_TYPE.get(provider_name)

    @staticmethod
    async def has_credential(
        db: AsyncSession, *, user_id: uuid.UUID, provider_type: str
    ) -> bool:
        result = await db.execute(
            select(UserCredential.id).where(
                UserCredential.user_id == user_id,
                UserCredential.provider_type == provider_type,
            )
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def delete_credential(
        db: AsyncSession, *, user_id: uuid.UUID, provider_type: str
    ) -> None:
        await db.execute(
            delete(UserCredential).where(
                UserCredential.user_id == user_id,
                UserCredential.provider_type == provider_type,
            )
        )
        await db.commit()
