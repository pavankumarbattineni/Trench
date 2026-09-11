"""Auto-generates a thread's title from the user's first message in it.

Always uses the platform's Groq default model regardless of the user's own
selected chat model (see LLMClientService.resolve_for_user) -- a title is
a cheap, throwaway classification task, not worth waiting on a slower or
BYOK-gated model for, and it must work even for users with no BYOK
credentials at all.
"""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.models import Thread
from app.service.provider_catalog_service import ProviderCatalogService

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "Generate a short, descriptive title (3-6 words) summarizing what the "
    "user is asking about. Respond with the title only -- no quotes, no "
    "trailing punctuation, nothing else."
)
_MAX_TITLE_LENGTH = 80


class ThreadTitleService:
    @staticmethod
    async def generate(db: AsyncSession, *, thread_id: uuid.UUID, query: str) -> None:
        """Best-effort: runs as a fire-and-forget background task (see
        ChatService.send_message) with nothing awaiting its result, so any
        failure here is logged and swallowed rather than propagated -- it
        must never be able to break the chat turn it's attached to.
        """
        try:
            title = await ThreadTitleService._complete(db, query)
        except Exception:
            logger.exception(
                "Thread title generation failed | thread_id=%s", thread_id
            )
            return

        if not title:
            logger.warning(
                "Thread title generation returned empty content | thread_id=%s",
                thread_id,
            )
            return

        thread = await db.get(Thread, thread_id)
        if thread is None or thread.title is not None:
            # Deleted, or already titled (e.g. a retried/duplicate call) --
            # nothing to do.
            return
        thread.title = title
        await db.commit()
        logger.info("Thread title generated | thread_id=%s title=%r", thread_id, title)

    @staticmethod
    async def _complete(db: AsyncSession, query: str) -> str:
        from groq import AsyncGroq

        default_model = await ProviderCatalogService.get_default(db)
        client = AsyncGroq(api_key=get_settings().TRENCH_CONFIG.GROQ.api_key)
        response = await client.chat.completions.create(
            model=default_model.model_name,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
            # The platform default (openai/gpt-oss-120b) is a reasoning
            # model -- its hidden reasoning tokens count against
            # max_tokens, and with a small budget they can consume it
            # entirely before any visible content is emitted (finish_reason
            # "length", content ""). reasoning_effort="low" keeps that
            # reasoning short; max_tokens=64 leaves headroom on top of it
            # for the actual title. Both are accepted no-ops on
            # non-reasoning models, so this stays safe if the platform
            # default ever changes.
            max_tokens=64,
            reasoning_effort="low",
        )
        content = response.choices[0].message.content or ""
        return content.strip().strip('"').strip()[:_MAX_TITLE_LENGTH]
