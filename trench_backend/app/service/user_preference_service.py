"""Resolves and lazily populates a user's selected LLM.

`User.model_id` starts out NULL and is filled in with the platform's
current default the first time it's read (e.g. by GET /users/me) -- there
is no "pick your model" onboarding step yet, but the column exists so the
frontend can start reflecting (and later, changing) a real per-user
selection without another migration. Embedding/chunking have no
equivalent column -- both are fixed, code-level choices.
"""

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Provider, ProviderModel, User
from app.service.credential_service import CredentialService
from app.service.provider_catalog_service import ProviderCatalogService

_MODEL_NOT_FOUND = HTTPException(
    status.HTTP_404_NOT_FOUND, "Model not found"
)
_MODEL_INACTIVE = HTTPException(
    status.HTTP_422_UNPROCESSABLE_CONTENT, "This model is no longer available"
)


class UserPreferenceService:
    @staticmethod
    async def ensure_defaults(db: AsyncSession, user: User) -> User:
        if user.model_id is not None:
            return user

        model = await ProviderCatalogService.get_default(db)
        user.model_id = model.id
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def update_model(db: AsyncSession, user: User, model_id) -> User:
        """Raises:
        HTTPException: 404 if `model_id` doesn't exist; 422 if it exists
            but has been deactivated (e.g. removed from the catalog), or
            if it belongs to a BYOK provider (OpenAI/Anthropic/Google) the
            user has no saved API key for -- selection-time validation, so
            a user can never end up with a model selected that generation
            can't actually honor (see LLMClientService.resolve_for_user,
            which would otherwise silently substitute the platform
            default without the user ever being told).
        """
        model = await db.get(ProviderModel, model_id)
        if model is None:
            raise _MODEL_NOT_FOUND
        if not model.is_active:
            raise _MODEL_INACTIVE

        provider = await db.get(Provider, model.provider_id)
        required_type = CredentialService.required_credential_type(provider.name)
        if required_type is not None:
            has_key = await CredentialService.has_credential(
                db, user_id=user.id, provider_type=required_type
            )
            if not has_key:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    f"This model requires a {provider.display_name} API key. "
                    "Add one in Settings first.",
                )

        user.model_id = model.id
        await db.commit()
        await db.refresh(user)
        return user
