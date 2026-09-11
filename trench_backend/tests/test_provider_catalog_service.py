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
