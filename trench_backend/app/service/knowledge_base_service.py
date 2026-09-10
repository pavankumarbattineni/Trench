"""Business logic for a user's (currently singular) KnowledgeBase.

Only one KnowledgeBase per user exists in this phase -- auto-created on
first use. Multi-KnowledgeBase support is a pure UI/API addition later;
nothing here needs to change for that.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import KnowledgeBase
from app.service.provider_catalog_service import ProviderCatalogService


class KnowledgeBaseService:
    @staticmethod
    async def get_or_create_default(
        db: AsyncSession, user_id: uuid.UUID
    ) -> KnowledgeBase:
        result = await db.execute(
            select(KnowledgeBase).where(KnowledgeBase.user_id == user_id)
        )
        knowledge_base = result.scalar_one_or_none()
        if knowledge_base is not None:
            return knowledge_base

        provider, model = await ProviderCatalogService.get_default_with_provider(
            db, "embedding"
        )
        knowledge_base = KnowledgeBase(
            user_id=user_id,
            embedding_provider=provider.name,
            embedding_model=model.model_name,
            embedding_dimensions=model.dimensions,
        )
        db.add(knowledge_base)
        await db.commit()
        await db.refresh(knowledge_base)
        return knowledge_base
