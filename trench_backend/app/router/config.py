"""A single endpoint returning the frontend-configurable LLM catalog, so
it's never hardcoded/duplicated in the frontend. Embedding and chunking
are fixed, code-level choices (see EmbeddingService/ChunkingService) --
not user-selectable, so there's nothing to list for them.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.database.session import get_db
from app.router.deps import get_current_user
from app.schemas.chat import KnowledgeType
from app.schemas.config import ConfigResponse, ModelResponse
from app.service.credential_service import CredentialService
from app.service.knowledge_access_service import KnowledgeAccessService
from app.service.provider_catalog_service import ProviderCatalogService

router = APIRouter(prefix="/config", tags=["config"])

_COMPANY_ACCESS_DENIED = HTTPException(
    status.HTTP_403_FORBIDDEN,
    "You don't have access to this tenant's company knowledge",
)


@router.get("", response_model=ConfigResponse)
async def get_config(
    knowledge_type: KnowledgeType = "personal",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConfigResponse:
    """Returns the available LLM models, each annotated with whether the
    caller can actually use it right now *for `knowledge_type`* -- a model
    is only ever usable with the credential belonging to the scope it's
    being used in (see LLMClientService.resolve_for_knowledge), so the
    listing has to branch the same way or it misleadingly shows a
    personal-BYOK model as available while Company knowledge is selected
    (and vice versa).

    Args:
        knowledge_type: Which knowledge base the caller is about to query
            -- "personal" credentials are the caller's own; "company"
            credentials are the caller's tenant's shared ones, and require
            the caller to actually have company-knowledge access.
        current_user: The authenticated user -- also used to look up which
            BYOK credentials apply, so each model can report
            `has_credential`.
        db: An active async SQLAlchemy session.

    Returns:
        Every active LLM model across all providers (Groq, OpenAI,
        Anthropic, Google), with exactly one flagged as the platform
        default, each annotated with `requires_api_key`/`has_credential`
        for the requested scope.

    Raises:
        HTTPException: 403 if `knowledge_type="company"` but the caller
            has no company-knowledge access.
    """
    pairs = await ProviderCatalogService.list_models_with_provider(db)

    if knowledge_type == "company":
        tenant_id = await KnowledgeAccessService.authorized_company_tenant_id(
            db, user_id=current_user.id
        )
        if tenant_id is None:
            raise _COMPANY_ACCESS_DENIED
        credentials = await CredentialService.list_credentials(db, tenant_id=tenant_id)
    else:
        credentials = await CredentialService.list_credentials(
            db, user_id=current_user.id
        )
    saved_provider_types = {c.provider_type for c in credentials}

    models = []
    for provider, model in pairs:
        required_type = CredentialService.required_credential_type(provider.name)
        models.append(
            ModelResponse(
                id=model.id,
                provider_name=provider.name,
                model_name=model.model_name,
                display_name=model.display_name,
                is_platform_default=model.is_platform_default,
                requires_api_key=required_type is not None,
                has_credential=(
                    required_type is None or required_type in saved_provider_types
                ),
            )
        )
    return ConfigResponse(llm_models=models)
