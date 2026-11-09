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
async def test_list_models_with_provider_lists_exactly_active_visible_models():
    """The user-facing listing (the model selector) shows every model that
    is both active and visible, and nothing else -- inactive models
    (removed from the catalog) and hidden backups (is_visible=False, still
    selectable by id) never appear. Checked against the table itself so
    the test holds for whatever catalog is seeded."""
    from sqlalchemy import select

    from app.database.models import ProviderModel

    async with async_session_factory() as session:
        pairs = await ProviderCatalogService.list_models_with_provider(session)
        result = await session.execute(
            select(ProviderModel.model_name).where(
                ProviderModel.is_active.is_(True),
                ProviderModel.is_visible.is_(True),
            )
        )
        expected = set(result.scalars().all())

    listed = [model.model_name for _provider, model in pairs]
    assert set(listed) == expected
    assert len(listed) == len(set(listed))  # no duplicates
    assert all(model.is_active and model.is_visible for _provider, model in pairs)


@pytest.mark.asyncio
async def test_only_the_seeded_groq_models_are_active():
    """Per the catalog seeded by scripts/seed_provider_catalog.py: only the
    platform default (openai/gpt-oss-120b) and qwen/qwen3.8-27b are active
    under Groq. qwen/qwen3.6-27b was retired by Groq and is no longer in
    the catalog."""
    from sqlalchemy import select

    from app.database.models import Provider, ProviderModel

    async with async_session_factory() as session:
        result = await session.execute(
            select(ProviderModel)
            .join(Provider, Provider.id == ProviderModel.provider_id)
            .where(Provider.name == "groq", ProviderModel.is_active.is_(True))
        )
        groq_model_names = {m.model_name for m in result.scalars().all()}

    assert groq_model_names == {"openai/gpt-oss-120b", "qwen/qwen3.8-27b"}
