import pytest

from app.database.session import async_session_factory
from app.service.provider_catalog_service import ProviderCatalogService


@pytest.mark.asyncio
async def test_get_default_is_seeded_groq_model():
    async with async_session_factory() as session:
        model = await ProviderCatalogService.get_default(session)

    assert model.model_name == "openai/gpt-oss-120b"


@pytest.mark.asyncio
async def test_list_models_with_provider_includes_all_four_providers():
    async with async_session_factory() as session:
        pairs = await ProviderCatalogService.list_models_with_provider(session)

    provider_names = {provider.name for provider, _model in pairs}
    assert {"groq", "openai", "anthropic", "google"}.issubset(provider_names)

    model_names = {model.model_name for _provider, model in pairs}
    assert "openai/gpt-oss-120b" in model_names
    # Embedding/rerank models are fixed, code-level choices now -- never
    # part of this catalog.
    assert "BAAI/bge-small-en-v1.5" not in model_names


@pytest.mark.asyncio
async def test_list_models_with_provider_excludes_hidden_backup_models():
    """qwen/qwen3.8-27b and qwen/qwen3.6-27b remain in the catalog as
    internal backup/fallback models (is_active stays True, so they're
    still directly selectable by id) but are hidden from this
    user-facing listing (is_visible=False)."""
    async with async_session_factory() as session:
        pairs = await ProviderCatalogService.list_models_with_provider(session)

    model_names = {model.model_name for _provider, model in pairs}
    assert "qwen/qwen3.8-27b" not in model_names
    assert "qwen/qwen3.6-27b" not in model_names


@pytest.mark.asyncio
async def test_only_three_groq_models_remain_in_the_catalog():
    """Per the pruned catalog: only the platform default
    (openai/gpt-oss-120b) and the two hidden Qwen backups remain under
    Groq -- compound/compound-mini/gpt-oss-20b/gpt-oss-safeguard-20b/
    allam-2-7b were removed."""
    from sqlalchemy import select

    from app.database.models import Provider, ProviderModel

    async with async_session_factory() as session:
        result = await session.execute(
            select(ProviderModel)
            .join(Provider, Provider.id == ProviderModel.provider_id)
            .where(Provider.name == "groq", ProviderModel.is_active.is_(True))
        )
        groq_model_names = {m.model_name for m in result.scalars().all()}

    assert groq_model_names == {
        "openai/gpt-oss-120b",
        "qwen/qwen3.8-27b",
        "qwen/qwen3.6-27b",
    }
