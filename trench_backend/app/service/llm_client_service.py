"""Resolves a user's selected chat model to a live streaming client call.

Groq is the platform-owned free default (its key lives in
TRENCH_CONFIG.GROQ, never per-user). OpenAI/Anthropic/Gemini require the
user's own validated personal BYOK credential for a personal-knowledge
query -- if a user's selected model belongs to one of those providers but
they have no stored credential for it, resolution raises
LLMClientService.MissingCredentialError rather than silently falling back
to the platform default; no provider's models may be used without a
valid key for that provider. (Company-knowledge queries are different:
falling back to the platform default when the tenant hasn't set a shared
credential for the resolved provider is expected, normal behavior, not a
missing-credential error -- see resolve_for_knowledge.)
"""

import uuid
from collections.abc import AsyncIterator
from functools import lru_cache

import tiktoken
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.models import Provider, ProviderModel, User
from app.service.credential_service import CredentialService
from app.service.provider_catalog_service import ProviderCatalogService

# Appended to an answer the model stopped because it hit its output limit,
# so a cut-off answer is never presented as if it were complete.
TRUNCATION_NOTE = (
    "\n\n---\n*This answer was cut off because it reached the model's output "
    'limit. Ask a follow-up such as "continue", or a narrower question, to '
    "get the rest.*"
)

# Sentinel the provider streams yield (never shown to users) when the model
# stopped at its output limit; stream_generate turns it into TRUNCATION_NOTE.
_TRUNCATED = "\x00__truncated__\x00"

# Groq output-limit sizing (see _groq_max_completion_tokens).
_GROQ_MIN_OUTPUT_TOKENS = 1024
_GROQ_MAX_OUTPUT_TOKENS = 16384
_GROQ_TOKEN_SAFETY_MARGIN = 400  # tokenizer estimate + per-message overhead


@lru_cache
def _encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding("o200k_base")


def _groq_max_completion_tokens(
    system_prompt: str, messages: list[dict[str, str]]
) -> int:
    """Output limit for one Groq request. Without one, Groq applies a
    3,072-token default that the model's hidden reasoning shares with the
    answer, cutting long answers off mid-sentence. Groq also counts prompt
    + this limit against the per-minute token quota, so it's sized to the
    room the prompt leaves in that budget (never below a usable minimum)."""
    encoding = _encoding()
    prompt_tokens = len(encoding.encode(system_prompt)) + sum(
        len(encoding.encode(m["content"])) + 8 for m in messages
    )
    budget = get_settings().TRENCH_CONFIG.GROQ.tokens_per_minute
    room = budget - prompt_tokens - _GROQ_TOKEN_SAFETY_MARGIN
    return max(_GROQ_MIN_OUTPUT_TOKENS, min(room, _GROQ_MAX_OUTPUT_TOKENS))


class ResolvedModel:
    def __init__(self, *, provider_name: str, model_name: str, api_key: str) -> None:
        self.provider_name = provider_name
        self.model_name = model_name
        self.api_key = api_key


class LLMClientService:
    class MissingCredentialError(Exception):
        """Raised when the user's selected model belongs to a BYOK
        provider they have no saved credential for. Selection-time
        validation (UserPreferenceService.update_model) normally prevents
        this from ever happening in practice -- this only fires if a
        credential is removed after a model using it was already
        selected. No provider's models may be used without a valid key
        for that provider, so this is a hard stop, never a silent
        fallback to the platform default."""

        def __init__(self, provider_display_name: str) -> None:
            self.provider_display_name = provider_display_name
            super().__init__(
                f"Your selected model requires a {provider_display_name} API "
                "key. Add one in Settings, or switch to a different model."
            )

    @staticmethod
    def _groq_resolved(model: ProviderModel) -> ResolvedModel:
        return ResolvedModel(
            provider_name="groq",
            model_name=model.model_name,
            api_key=get_settings().TRENCH_CONFIG.GROQ.api_key,
        )

    @staticmethod
    async def _resolve_byok_key(
        db: AsyncSession,
        provider_name: str,
        *,
        user_id: uuid.UUID | None = None,
        tenant_id: uuid.UUID | None = None,
    ) -> str | None:
        """The decrypted BYOK key for `provider_name` in exactly one of
        the personal (user_id) or tenant scopes, or None if that provider
        needs no credential (Groq) or the scope has none stored."""
        byok_type = CredentialService.required_credential_type(provider_name)
        if byok_type is None:
            return None
        return await CredentialService.get_decrypted(
            db, user_id=user_id, tenant_id=tenant_id, provider_type=byok_type
        )

    @staticmethod
    async def resolve_platform_default(db: AsyncSession) -> ResolvedModel:
        """The platform-owned Groq default model/key -- for internal
        rewrite steps (condense_query, reformulate_query) that must never
        consume the user's own selected model or BYOK credential just to
        rewrite a search query, and must never fail the turn over a
        missing BYOK credential for what is only an optimization step."""
        default_model = await ProviderCatalogService.get_default(db)
        return LLMClientService._groq_resolved(default_model)

    @staticmethod
    async def resolve_for_user(db: AsyncSession, user: User) -> ResolvedModel:
        default_model = await ProviderCatalogService.get_default(db)
        model_id = user.model_id or default_model.id
        model = await db.get(ProviderModel, model_id) or default_model
        provider = await db.get(Provider, model.provider_id)

        if provider.name == "groq":
            return LLMClientService._groq_resolved(model)

        personal_key = await LLMClientService._resolve_byok_key(
            db, provider.name, user_id=user.id
        )
        if personal_key is not None:
            return ResolvedModel(
                provider_name=provider.name,
                model_name=model.model_name,
                api_key=personal_key,
            )

        raise LLMClientService.MissingCredentialError(provider.display_name)

    @staticmethod
    async def resolve_for_knowledge(
        db: AsyncSession,
        user: User,
        *,
        knowledge_type: str,
        tenant_id: uuid.UUID | None,
    ) -> ResolvedModel:
        """Resolves which model/credential a query should use, branching
        on knowledge_type:

        - "personal" (or no tenant): identical to resolve_for_user (the
          querying user's own personal BYOK credential -- a hard
          MissingCredentialError if absent for a BYOK model).
        - "company": the tenant's shared (tenant-scoped) credential if the
          Owner has set one for the resolved provider; otherwise silently
          the platform default. Never falls back to the querying member's
          own personal credential, even if they have one -- a member's
          personal key only ever powers their personal KB.

        Both scopes live in the same `credentials` table now; this
        asymmetry is deliberately decided here, not by storage.
        """
        if knowledge_type == "personal" or tenant_id is None:
            return await LLMClientService.resolve_for_user(db, user)

        default_model = await ProviderCatalogService.get_default(db)
        model = await db.get(ProviderModel, user.model_id) if user.model_id else None
        model = model or default_model
        provider = await db.get(Provider, model.provider_id)

        if provider.name == "groq":
            return LLMClientService._groq_resolved(model)

        tenant_key = await LLMClientService._resolve_byok_key(
            db, provider.name, tenant_id=tenant_id
        )
        if tenant_key is not None:
            return ResolvedModel(
                provider_name=provider.name,
                model_name=model.model_name,
                api_key=tenant_key,
            )

        return LLMClientService._groq_resolved(default_model)

    @staticmethod
    async def stream_generate(
        resolved: ResolvedModel,
        *,
        system_prompt: str,
        messages: list[dict[str, str]],
    ) -> AsyncIterator[str]:
        """Streams the model's text. If the model stopped because it hit
        its output limit, the stream ends with TRUNCATION_NOTE instead of
        silently stopping mid-sentence. Callers that use the text as data
        rather than as an answer (the query-rewrite steps) strip it with
        `str.removesuffix(TRUNCATION_NOTE)`."""
        providers = {
            "groq": LLMClientService._stream_groq,
            "openai": LLMClientService._stream_openai,
            "anthropic": LLMClientService._stream_anthropic,
            "google": LLMClientService._stream_gemini,
        }
        stream_fn = providers.get(resolved.provider_name)
        if stream_fn is None:
            raise ValueError(f"Unsupported LLM provider: {resolved.provider_name!r}")

        truncated = False
        async for delta in stream_fn(
            resolved, system_prompt=system_prompt, messages=messages
        ):
            if delta == _TRUNCATED:
                truncated = True
                continue
            yield delta
        if truncated:
            yield TRUNCATION_NOTE

    @staticmethod
    async def _stream_groq(
        resolved: ResolvedModel, *, system_prompt: str, messages: list[dict[str, str]]
    ) -> AsyncIterator[str]:
        from groq import AsyncGroq

        client = AsyncGroq(api_key=resolved.api_key)
        stream = await client.chat.completions.create(
            model=resolved.model_name,
            messages=[{"role": "system", "content": system_prompt}, *messages],
            max_completion_tokens=_groq_max_completion_tokens(system_prompt, messages),
            stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.delta.content:
                yield choice.delta.content
            if choice.finish_reason == "length":
                yield _TRUNCATED

    @staticmethod
    async def _stream_openai(
        resolved: ResolvedModel, *, system_prompt: str, messages: list[dict[str, str]]
    ) -> AsyncIterator[str]:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=resolved.api_key)
        stream = await client.chat.completions.create(
            model=resolved.model_name,
            messages=[{"role": "system", "content": system_prompt}, *messages],
            stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.delta.content:
                yield choice.delta.content
            if choice.finish_reason == "length":
                yield _TRUNCATED

    @staticmethod
    async def _stream_anthropic(
        resolved: ResolvedModel, *, system_prompt: str, messages: list[dict[str, str]]
    ) -> AsyncIterator[str]:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=resolved.api_key)
        async with client.messages.stream(
            model=resolved.model_name,
            max_tokens=2048,
            system=system_prompt,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text
            final = await stream.get_final_message()
            if final.stop_reason == "max_tokens":
                yield _TRUNCATED

    @staticmethod
    async def _stream_gemini(
        resolved: ResolvedModel, *, system_prompt: str, messages: list[dict[str, str]]
    ) -> AsyncIterator[str]:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=resolved.api_key)
        conversation = "\n\n".join(f"{m['role']}: {m['content']}" for m in messages)
        prompt = f"{system_prompt}\n\n{conversation}"
        stream = await client.aio.models.generate_content_stream(
            model=resolved.model_name, contents=prompt
        )
        async for chunk in stream:
            if chunk.text:
                yield chunk.text
            candidates = chunk.candidates or []
            if (
                candidates
                and candidates[0].finish_reason == types.FinishReason.MAX_TOKENS
            ):
                yield _TRUNCATED
