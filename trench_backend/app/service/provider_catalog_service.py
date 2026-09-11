"""Reads LLM model configuration from the database.

Application code never hardcodes a provider's model name -- it always
resolves "which model do we use" through this service. Embedding/rerank
models are NOT part of this catalog (see EmbeddingService) -- they're
fixed, code-level choices, not user-selectable, so there's nothing to
look up for them.
"""

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.models import Provider, ProviderModel

# Groq's /models endpoint also returns audio transcription, text-to-speech,
# and safety/prompt-guard classifier models -- not general-purpose text
# chat models, so they're excluded from the catalog even though some are
# technically text-in/text-out.
_GROQ_NON_CHAT_MODEL_PATTERNS = ("whisper", "orpheus", "prompt-guard")


class ProviderCatalogService:
    @staticmethod
    async def get_default(db: AsyncSession) -> ProviderModel:
        """Returns the platform-default LLM model.

        Raises:
            RuntimeError: if no default is configured -- a seeding bug, not
                something calling code should silently work around.
        """
        result = await db.execute(
            select(ProviderModel).where(ProviderModel.is_platform_default.is_(True))
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise RuntimeError("No platform default LLM model configured")
        return model

    @staticmethod
    async def list_models_with_provider(
        db: AsyncSession,
    ) -> list[tuple[Provider, ProviderModel]]:
        result = await db.execute(
            select(Provider, ProviderModel)
            .join(ProviderModel, ProviderModel.provider_id == Provider.id)
            .where(ProviderModel.is_active.is_(True))
        )
        return [(row[0], row[1]) for row in result.all()]

    @classmethod
    async def sync_groq_models(
        cls, db: AsyncSession, *, default_model_name: str | None = None
    ) -> list[ProviderModel]:
        """Refreshes Groq's provider_models rows from Groq's live /models
        endpoint. Safe to run repeatedly -- upserts by model_name, never
        duplicates.
        """
        api_key = get_settings().TRENCH_CONFIG.GROQ.api_key
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
        models_data = response.json()["data"]

        result = await db.execute(select(Provider).where(Provider.name == "groq"))
        provider = result.scalar_one_or_none()
        if provider is None:
            provider = Provider(name="groq", display_name="Groq")
            db.add(provider)
            await db.flush()

        for entry in models_data:
            model_id = entry["id"]
            if any(pattern in model_id for pattern in _GROQ_NON_CHAT_MODEL_PATTERNS):
                continue
            if "text" not in entry.get(
                "input_modalities", []
            ) or "text" not in entry.get("output_modalities", []):
                continue

            result = await db.execute(
                select(ProviderModel).where(
                    ProviderModel.provider_id == provider.id,
                    ProviderModel.model_name == model_id,
                )
            )
            model = result.scalar_one_or_none()
            if model is None:
                model = ProviderModel(
                    provider_id=provider.id,
                    model_name=model_id,
                    display_name=entry.get("name", model_id),
                )
                db.add(model)
            else:
                model.display_name = entry.get("name", model_id)
                model.is_active = True

            if default_model_name is not None and model_id == default_model_name:
                await db.execute(
                    update(ProviderModel)
                    .where(ProviderModel.is_platform_default.is_(True))
                    .values(is_platform_default=False)
                )
                model.is_platform_default = True

        await db.commit()
        result = await db.execute(
            select(ProviderModel).where(ProviderModel.provider_id == provider.id)
        )
        return list(result.scalars().all())
