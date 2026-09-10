import pytest

from app.database.session import async_session_factory
from app.service.provider_catalog_service import ProviderCatalogService


@pytest.mark.asyncio
async def test_get_default_llm_is_seeded_groq_model():
    async with async_session_factory() as session:
        model = await ProviderCatalogService.get_default(session, "llm")

    assert model.model_name == "openai/gpt-oss-120b"


@pytest.mark.asyncio
async def test_get_default_embedding_is_local_bge_small():
    async with async_session_factory() as session:
        model = await ProviderCatalogService.get_default(session, "embedding")

    assert model.model_name == "BAAI/bge-small-en-v1.5"
    assert model.dimensions == 384


@pytest.mark.asyncio
async def test_get_default_rerank_is_local_cross_encoder():
    async with async_session_factory() as session:
        model = await ProviderCatalogService.get_default(session, "rerank")

    assert model.model_name == "cross-encoder/ms-marco-MiniLM-L-6-v2"


@pytest.mark.asyncio
async def test_get_default_raises_for_unconfigured_model_type():
    async with async_session_factory() as session:
        with pytest.raises(RuntimeError):
            await ProviderCatalogService.get_default(session, "parsing")


@pytest.mark.asyncio
async def test_list_models_returns_only_active_models_of_requested_type():
    async with async_session_factory() as session:
        llm_models = await ProviderCatalogService.list_models(session, "llm")

    model_names = {model.model_name for model in llm_models}
    assert "openai/gpt-oss-120b" in model_names
    assert "BAAI/bge-small-en-v1.5" not in model_names
