"""A single endpoint returning the frontend-configurable LLM catalog, so
it's never hardcoded/duplicated in the frontend. Embedding and chunking
are fixed, code-level choices (see EmbeddingService/ChunkingService) --
not user-selectable, so there's nothing to list for them.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.database.session import get_db
from app.router.deps import get_current_user
from app.schemas.config import ConfigResponse, ModelResponse
from app.service.provider_catalog_service import ProviderCatalogService

router = APIRouter(prefix="/config", tags=["config"])


@router.get("", response_model=ConfigResponse)
async def get_config(
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConfigResponse:
    """Returns the available LLM models.

    Args:
        _current_user: The authenticated user (only used to require auth).
        db: An active async SQLAlchemy session.

    Returns:
        Every active LLM model across all providers (Groq, OpenAI,
        Anthropic, Google), with exactly one flagged as the platform
        default.
    """
    pairs = await ProviderCatalogService.list_models_with_provider(db)
    return ConfigResponse(
        llm_models=[
            ModelResponse(
                id=model.id,
                provider_name=provider.name,
                model_name=model.model_name,
                display_name=model.display_name,
                is_platform_default=model.is_platform_default,
            )
            for provider, model in pairs
        ]
    )
