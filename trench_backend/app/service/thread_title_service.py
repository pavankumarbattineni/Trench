"""Auto-generates a thread's title from the user's first message in it.

Always uses the platform's Groq default model regardless of the user's own
selected chat model (see LLMClientService.resolve_for_user) -- a title is
a cheap, throwaway classification task, not worth waiting on a slower or
BYOK-gated model for, and it must work even for users with no BYOK
credentials at all.

The model sometimes *answers* the message instead of titling it ("I'm
sorry, but I can't help with that."), so every candidate is checked
before it's saved: one retry, then a title built from the message's own
key words. A reply is never saved as a title.
"""

import logging
import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.models import Thread
from app.service.provider_catalog_service import ProviderCatalogService
from app.utils import usage_tracking

logger = logging.getLogger(__name__)

MAX_TITLE_WORDS = 6
_MAX_TITLE_LENGTH = 60

_SYSTEM_PROMPT = (
    "You label chat conversations. Given the user's first message, write a "
    f"title of 2 to {MAX_TITLE_WORDS} words that names what the user wants. "
    "Never answer, refuse, comment on or ask about the message -- only "
    "label it. Reply with the title alone: no quotes, no trailing "
    "punctuation.\n\n"
    "Examples:\n"
    "Message: I want to understand how my home loan EMIs are calculated "
    "and what happens if I prepay -> Home Loan EMI Analysis\n"
    "Message: Can you help me? -> Request for Assistance\n"
    "Message: Compare the Basic and Pro plans -> Basic vs Pro Plan "
    "Comparison\n"
    "Message: Something you can't or won't help with -> name its topic "
    "anyway, e.g. Medical Dosage Question"
)
_RETRY_REMINDER = (
    "Your previous reply was not a title. Reply with ONLY a "
    f"{MAX_TITLE_WORDS}-word-or-shorter title naming the topic -- do not "
    "answer or refuse the message."
)

# Signs the model replied to the message rather than labelling it.
_REPLY_PATTERN = re.compile(
    r"\b(i|i'm|im|i am|i'll|i can|i cannot|i can't|my|me|you|your|sorry|"
    r"unfortunately|sure|certainly|happy to|glad to|help you|here's|"
    r"here is|cannot|can't|unable)\b",
    re.IGNORECASE,
)
_SMALL_WORDS = {
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on",
    "or", "the", "to", "vs", "via", "with",
}  # fmt: skip
_STOPWORDS = _SMALL_WORDS | {
    "i", "im", "i'm", "me", "my", "we", "our", "you", "your", "it", "its",
    "is", "are", "was", "be", "been", "am", "do", "does", "did", "can",
    "could", "would", "should", "will", "please", "want", "need", "like",
    "really", "just", "how", "what", "why", "when", "where", "which",
    "who", "this", "that", "these", "those", "about", "help", "tell",
    "explain", "give", "show", "some", "any", "so", "if", "into", "right",
    "now", "currently", "hi", "hello", "hey", "thanks", "there", "have",
    "has", "get", "know", "understand", "trying", "try", "set", "up", "own",
    "make", "let", "lets", "let's",
}  # fmt: skip


def _title_case(words: list[str]) -> str:
    out = []
    for i, word in enumerate(words):
        lower = word.lower()
        if i > 0 and lower in _SMALL_WORDS:
            out.append(lower)
        elif word.isupper() or any(c.isupper() for c in word[1:]):
            out.append(word)  # acronyms and mixed case: EMI, DSA, iPhone
        else:
            out.append(word[:1].upper() + word[1:])
    return " ".join(out)


def clean_title(raw: str | None) -> str | None:
    """Normalises a model's candidate title, or returns None if it isn't
    a usable title (empty, too long, or a reply rather than a label)."""
    if not raw:
        return None
    text = raw.replace("’", "'").replace("‘", "'").strip()
    line = text.splitlines()[0] if text else ""
    line = re.sub(r"^(title\s*:\s*)", "", line, flags=re.IGNORECASE)
    line = line.strip().strip("\"'`*#_ ").strip()
    if not line or line.endswith("?"):
        return None
    line = line.rstrip(".!:;,").strip()
    words = line.split()
    if not words or len(words) > MAX_TITLE_WORDS or _REPLY_PATTERN.search(line):
        return None
    title = _title_case(words)
    return title if len(title) <= _MAX_TITLE_LENGTH else None


def fallback_title(query: str) -> str:
    """A title from the message's own key words, for when the model won't
    produce one."""
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'+.-]*", query)
    keep = [w.strip(".'") for w in words if w.lower().strip(".'") not in _STOPWORDS]
    keep = [w for w in keep if w][:4]
    return _title_case(keep) if keep else "Request for Assistance"


class ThreadTitleService:
    @staticmethod
    async def generate(db: AsyncSession, *, thread_id: uuid.UUID, query: str) -> None:
        """Best-effort: runs as a fire-and-forget background task (see
        ChatService.send_message) with nothing awaiting its result, so any
        failure here is logged and swallowed rather than propagated -- it
        must never be able to break the chat turn it's attached to.
        """
        title = None
        try:
            title = clean_title(await ThreadTitleService._complete(db, query))
            if title is None:
                title = clean_title(
                    await ThreadTitleService._complete(db, query, retry=True)
                )
        except Exception:
            logger.exception("Thread title generation failed | thread_id=%s", thread_id)
            return
        if title is None:
            title = fallback_title(query)
            logger.info("Thread title fell back to key words | thread_id=%s", thread_id)

        thread = await db.get(Thread, thread_id)
        if thread is None or thread.title is not None:
            # Deleted, or already titled (e.g. a retried/duplicate call) --
            # nothing to do.
            return
        thread.title = title
        await db.commit()
        logger.info("Thread title generated | thread_id=%s title=%r", thread_id, title)

    @staticmethod
    async def _complete(db: AsyncSession, query: str, *, retry: bool = False) -> str:
        from groq import AsyncGroq

        default_model = await ProviderCatalogService.get_default(db)
        client = AsyncGroq(api_key=get_settings().TRENCH_CONFIG.GROQ.api_key)
        # The message is quoted as data to label, not a request to act on.
        user_message = f'Message: """{query[:2000]}"""\nTitle:'
        if retry:
            user_message = f"{_RETRY_REMINDER}\n\n{user_message}"
        response = await client.chat.completions.create(
            model=default_model.model_name,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
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
        usage = getattr(response, "usage", None)
        if usage is not None:
            details = getattr(usage, "prompt_tokens_details", None)
            usage_tracking.record(
                usage_tracking.LLMCallUsage(
                    step=usage_tracking.current_step(),
                    provider_name="groq",
                    model_name=default_model.model_name,
                    key_owner="platform",
                    input_tokens=usage.prompt_tokens or 0,
                    cached_input_tokens=getattr(details, "cached_tokens", 0) or 0,
                    output_tokens=usage.completion_tokens or 0,
                )
            )
        return response.choices[0].message.content or ""
