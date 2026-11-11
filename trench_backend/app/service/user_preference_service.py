"""Resolves and lazily populates a user's selected LLM.

`User.model_id` starts out NULL and is filled in with the platform's
current default the first time it's read (e.g. by GET /users/me) -- there
is no "pick your model" onboarding step yet, but the column exists so the
frontend can start reflecting (and later, changing) a real per-user
selection without another migration. Embedding/chunking have no
equivalent column -- both are fixed, code-level choices.
"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Credential, Provider, ProviderModel, User
from app.schemas.chat import KnowledgeType
from app.service.credential_service import (
    LLM_PROVIDER_TO_CREDENTIAL_TYPE,
    CredentialService,
)
from app.service.knowledge_access_service import KnowledgeAccessService
from app.service.provider_catalog_service import ProviderCatalogService

_MODEL_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "Model not found")
_MODEL_INACTIVE = HTTPException(
    status.HTTP_422_UNPROCESSABLE_CONTENT, "This model is no longer available"
)
_COMPANY_ACCESS_DENIED = HTTPException(
    status.HTTP_403_FORBIDDEN,
    "You don't have access to this tenant's company knowledge",
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
    async def update_model(
        db: AsyncSession,
        user: User,
        model_id,
        *,
        knowledge_type: KnowledgeType = "personal",
    ) -> User:
        """Raises:
        HTTPException: 404 if `model_id` doesn't exist; 403 if
            `knowledge_type="company"` but the user has no company
            knowledge access; 422 if the model has been deactivated (e.g.
            removed from the catalog), or if it belongs to a BYOK provider
            (OpenAI/Anthropic/Google) that the relevant scope -- the
            user's own credential for "personal", their tenant's shared
            one for "company" -- has no saved API key for. Selection-time
            validation, so a user can never end up with a model selected
            that generation can't actually honor for the scope they picked
            it in (see LLMClientService.resolve_for_knowledge, which would
            otherwise silently substitute the platform default without the
            user ever being told).
        """
        model = await db.get(ProviderModel, model_id)
        if model is None:
            raise _MODEL_NOT_FOUND
        if not model.is_active:
            raise _MODEL_INACTIVE

        provider = await db.get(Provider, model.provider_id)
        required_type = CredentialService.required_credential_type(provider.name)
        if required_type is not None:
            if knowledge_type == "company":
                tenant_id = await KnowledgeAccessService.authorized_company_tenant_id(
                    db, user_id=user.id
                )
                if tenant_id is None:
                    raise _COMPANY_ACCESS_DENIED
                has_key = await CredentialService.has_credential(
                    db, tenant_id=tenant_id, provider_type=required_type
                )
            else:
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

    @staticmethod
    async def reset_models_requiring_credential(
        db: AsyncSession,
        provider_type: str,
        *,
        user_id: uuid.UUID | None = None,
        tenant_id: uuid.UUID | None = None,
    ) -> int:
        """Resets `model_id` to the platform default for users whose
        selected model needs the BYOK credential (`provider_type`) that was
        just removed -- so the model selector never keeps showing a model
        that can no longer run. Call right after deleting that credential.

        - Personal removal (`user_id`): resets that user, if their model
          belongs to a provider needing this credential type.
        - Tenant removal (`tenant_id`): resets every member of that tenant
          whose model needs it -- except members who still hold their own
          personal credential of the same type, for whom the model still
          works.

        Returns how many users were reset. A no-op for credential types no
        LLM provider needs.
        """
        provider_names = [
            name
            for name, cred_type in LLM_PROVIDER_TO_CREDENTIAL_TYPE.items()
            if cred_type == provider_type
        ]
        if not provider_names:
            return 0

        default_model = await ProviderCatalogService.get_default(db)
        affected_model_ids = (
            select(ProviderModel.id)
            .join(Provider, Provider.id == ProviderModel.provider_id)
            .where(Provider.name.in_(provider_names))
        )
        stmt = update(User).where(User.model_id.in_(affected_model_ids))
        if user_id is not None:
            stmt = stmt.where(User.id == user_id)
        else:
            has_personal_key = (
                select(Credential.id)
                .where(
                    Credential.user_id == User.id,
                    Credential.provider_type == provider_type,
                )
                .exists()
            )
            stmt = stmt.where(User.tenant_id == tenant_id, ~has_personal_key)

        result = await db.execute(
            stmt.values(model_id=default_model.id).execution_options(
                synchronize_session=False
            )
        )
        await db.commit()
        return result.rowcount
