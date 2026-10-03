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

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.models import Provider, ProviderModel, User
from app.service.credential_service import CredentialService
from app.service.provider_catalog_service import ProviderCatalogService


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
        if resolved.provider_name == "groq":
            async for delta in LLMClientService._stream_groq(
                resolved, system_prompt=system_prompt, messages=messages
            ):
                yield delta
        elif resolved.provider_name == "openai":
            async for delta in LLMClientService._stream_openai(
                resolved, system_prompt=system_prompt, messages=messages
            ):
                yield delta
        elif resolved.provider_name == "anthropic":
            async for delta in LLMClientService._stream_anthropic(
                resolved, system_prompt=system_prompt, messages=messages
            ):
                yield delta
        elif resolved.provider_name == "google":
            async for delta in LLMClientService._stream_gemini(
                resolved, system_prompt=system_prompt, messages=messages
            ):
                yield delta
        else:
            raise ValueError(f"Unsupported LLM provider: {resolved.provider_name!r}")

    @staticmethod
    async def _stream_groq(
        resolved: ResolvedModel, *, system_prompt: str, messages: list[dict[str, str]]
    ) -> AsyncIterator[str]:
        from groq import AsyncGroq

        client = AsyncGroq(api_key=resolved.api_key)
        stream = await client.chat.completions.create(
            model=resolved.model_name,
            messages=[{"role": "system", "content": system_prompt}, *messages],
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

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
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

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

    @staticmethod
    async def _stream_gemini(
        resolved: ResolvedModel, *, system_prompt: str, messages: list[dict[str, str]]
    ) -> AsyncIterator[str]:
        from google import genai

        client = genai.Client(api_key=resolved.api_key)
        conversation = "\n\n".join(f"{m['role']}: {m['content']}" for m in messages)
        prompt = f"{system_prompt}\n\n{conversation}"
        stream = await client.aio.models.generate_content_stream(
            model=resolved.model_name, contents=prompt
        )
        async for chunk in stream:
            if chunk.text:
                yield chunk.text
