"""Reads model configuration from the database.

Application code never hardcodes a provider's model name -- it always
resolves "which model do we use for X" through this service.
"""

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.models import Provider, ProviderModel

# Groq's /models endpoint also returns audio transcription, text-to-speech,
# and safety/prompt-guard classifier models -- not general-purpose text
# chat models, so they're excluded from the "llm" catalog even though some
# are technically text-in/text-out.
_GROQ_NON_CHAT_MODEL_PATTERNS = ("whisper", "orpheus", "prompt-guard")


class ProviderCatalogService:
    @staticmethod
    async def get_default(db: AsyncSession, model_type: str) -> ProviderModel:
        """Returns the platform-default model for a purpose (llm/embedding/rerank).

        Raises:
            RuntimeError: if no default is configured -- a seeding bug, not
                something calling code should silently work around.
        """
        result = await db.execute(
            select(ProviderModel).where(
                ProviderModel.model_type == model_type,
                ProviderModel.is_platform_default.is_(True),
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise RuntimeError(
                f"No platform default configured for model_type={model_type!r}"
            )
        return model

    @staticmethod
    async def get_default_with_provider(
        db: AsyncSession, model_type: str
    ) -> tuple[Provider, ProviderModel]:
        """Same as get_default, but also returns the owning Provider row --
        needed wherever code has to record which provider a model came
        from (e.g. KnowledgeBase.embedding_provider)."""
        model = await ProviderCatalogService.get_default(db, model_type)
        result = await db.execute(
            select(Provider).where(Provider.id == model.provider_id)
        )
        return result.scalar_one(), model

    @staticmethod
    async def list_models(
        db: AsyncSession, model_type: str, *, provider_name: str | None = None
    ) -> list[ProviderModel]:
        query = select(ProviderModel).where(
            ProviderModel.model_type == model_type, ProviderModel.is_active.is_(True)
        )
        if provider_name is not None:
            query = query.join(Provider).where(Provider.name == provider_name)
        result = await db.execute(query)
        return list(result.scalars().all())

    @classmethod
    async def sync_groq_models(
        cls, db: AsyncSession, *, default_model_name: str | None = None
    ) -> list[ProviderModel]:
        """Refreshes Groq's `llm` provider_models rows from Groq's live
        /models endpoint. Safe to run repeatedly -- upserts by model_name,
        never duplicates.
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
                    model_type="llm",
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
                    .where(
                        ProviderModel.model_type == "llm",
                        ProviderModel.is_platform_default.is_(True),
                    )
                    .values(is_platform_default=False)
                )
                model.is_platform_default = True

        await db.commit()
        result = await db.execute(
            select(ProviderModel).where(ProviderModel.provider_id == provider.id)
        )
        return list(result.scalars().all())
