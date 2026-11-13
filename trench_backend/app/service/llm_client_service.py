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
from langsmith import trace as ls_trace
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.models import Provider, ProviderModel, User
from app.service.credential_service import CredentialService
from app.service.provider_catalog_service import ProviderCatalogService
from app.utils import usage_tracking

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


class _ProviderUsage:
    """Yielded once, last, by a provider stream: the token usage the
    provider reported. stream_generate consumes it (never passes it on)."""

    def __init__(
        self, *, input_tokens: int, cached_input_tokens: int, output_tokens: int
    ) -> None:
        self.input_tokens = input_tokens
        self.cached_input_tokens = cached_input_tokens
        self.output_tokens = output_tokens


def _openai_style_usage(usage) -> _ProviderUsage:
    """OpenAI/Groq chat-completions usage: prompt_tokens already includes
    cached tokens (prompt_tokens_details.cached_tokens)."""
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    return _ProviderUsage(
        input_tokens=usage.prompt_tokens or 0,
        cached_input_tokens=cached,
        output_tokens=usage.completion_tokens or 0,
    )


# Groq output-limit sizing (see _groq_max_completion_tokens).
_GROQ_MIN_OUTPUT_TOKENS = 1024
_GROQ_MAX_OUTPUT_TOKENS = 16384
_GROQ_TOKEN_SAFETY_MARGIN = 400  # tokenizer estimate + per-message overhead


_PROVIDER_LABELS = {
    "groq": "Groq",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Gemini",
}


def _trace_outputs(text: str, usage: _ProviderUsage | None, truncated: bool) -> dict:
    """The LangSmith "llm" run's outputs: the answer as a chat completion,
    plus usage_metadata (which LangSmith reads for tokens and cost) when the
    provider reported it."""
    outputs: dict = {
        "choices": [
            {
                "message": {"role": "assistant", "content": text},
                "finish_reason": "length" if truncated else "stop",
            }
        ]
    }
    if usage is not None:
        outputs["usage_metadata"] = {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.input_tokens + usage.output_tokens,
            "input_token_details": {"cache_read": usage.cached_input_tokens},
        }
    return outputs


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
    def __init__(
        self,
        *,
        provider_name: str,
        model_name: str,
        api_key: str,
        key_owner: str = "platform",
    ) -> None:
        self.provider_name = provider_name
        self.model_name = model_name
        self.api_key = api_key
        # Whose key pays for calls: "platform" (Trench's own Groq key),
        # "personal" (the user's BYOK key) or "tenant" (the shared key).
        self.key_owner = key_owner


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
                key_owner="personal",
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
                key_owner="tenant",
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
        usage: _ProviderUsage | None = None
        output: list[str] = []
        # The provider SDKs aren't LangChain models, so the call is recorded
        # as an "llm" run by hand; it nests under the current graph node.
        # No-op unless LangSmith tracing is enabled.
        with ls_trace(
            name=f"{_PROVIDER_LABELS.get(resolved.provider_name, 'LLM')} chat",
            run_type="llm",
            inputs={
                "messages": [{"role": "system", "content": system_prompt}, *messages]
            },
            metadata={
                "ls_provider": resolved.provider_name,
                "ls_model_name": resolved.model_name,
                "key_owner": resolved.key_owner,
            },
        ) as run:
            try:
                async for delta in stream_fn(
                    resolved, system_prompt=system_prompt, messages=messages
                ):
                    if isinstance(delta, _ProviderUsage):
                        usage = delta
                        continue
                    if delta == _TRUNCATED:
                        truncated = True
                        continue
                    output.append(delta)
                    yield delta
                if truncated:
                    yield TRUNCATION_NOTE
            finally:
                # Runs on completion, error and cancellation alike (a stopped
                # answer still used tokens). No-op unless a turn is collecting.
                text = "".join(output)
                LLMClientService._record_usage(
                    resolved, usage, system_prompt, messages, text
                )
                run.end(outputs=_trace_outputs(text, usage, truncated))

    @staticmethod
    def _record_usage(
        resolved: ResolvedModel,
        usage: _ProviderUsage | None,
        system_prompt: str,
        messages: list[dict[str, str]],
        output_text: str,
    ) -> None:
        estimated = usage is None
        if usage is None:
            # The provider never reported usage (stream stopped early, or it
            # doesn't report on interruption): estimate with the tokenizer.
            encoding = _encoding()
            usage = _ProviderUsage(
                input_tokens=len(encoding.encode(system_prompt))
                + sum(len(encoding.encode(m["content"])) + 4 for m in messages),
                cached_input_tokens=0,
                output_tokens=len(encoding.encode(output_text)),
            )
        usage_tracking.record(
            usage_tracking.LLMCallUsage(
                step=usage_tracking.current_step(),
                provider_name=resolved.provider_name,
                model_name=resolved.model_name,
                key_owner=getattr(resolved, "key_owner", "platform"),
                input_tokens=usage.input_tokens,
                cached_input_tokens=usage.cached_input_tokens,
                output_tokens=usage.output_tokens,
                estimated=estimated,
            )
        )

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
            # Groq always reports usage on the final chunk under x_groq
            # (newer API versions also mirror it at the top level). The
            # Groq SDK has no stream_options parameter, so none is sent.
            x_groq = getattr(chunk, "x_groq", None)
            usage = getattr(chunk, "usage", None) or getattr(x_groq, "usage", None)
            if usage is not None:
                yield _openai_style_usage(usage)
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
            stream_options={"include_usage": True},
        )
        async for chunk in stream:
            if getattr(chunk, "usage", None) is not None:
                # The final chunk carries usage (and no choices).
                yield _openai_style_usage(chunk.usage)
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
            if final.usage is not None:
                cache_read = final.usage.cache_read_input_tokens or 0
                cache_write = final.usage.cache_creation_input_tokens or 0
                yield _ProviderUsage(
                    # Anthropic reports cache reads/writes apart from
                    # input_tokens; Trench's input count includes them.
                    input_tokens=final.usage.input_tokens + cache_read + cache_write,
                    cached_input_tokens=cache_read,
                    output_tokens=final.usage.output_tokens,
                )

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
        last_usage = None
        async for chunk in stream:
            if getattr(chunk, "usage_metadata", None) is not None:
                last_usage = chunk.usage_metadata
            if chunk.text:
                yield chunk.text
            candidates = chunk.candidates or []
            if (
                candidates
                and candidates[0].finish_reason == types.FinishReason.MAX_TOKENS
            ):
                yield _TRUNCATED
        if last_usage is not None:
            # prompt_token_count already includes cached_content_token_count;
            # thinking tokens are billed as output.
            yield _ProviderUsage(
                input_tokens=last_usage.prompt_token_count or 0,
                cached_input_tokens=last_usage.cached_content_token_count or 0,
                output_tokens=(last_usage.candidates_token_count or 0)
                + (getattr(last_usage, "thoughts_token_count", 0) or 0),
            )
