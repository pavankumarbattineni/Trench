"""Resolves a user's selected chat model to a live streaming client call.

Groq is the platform-owned free default (its key lives in
TRENCH_CONFIG.GROQ, never per-user). OpenAI/Anthropic/Gemini require the
user's own validated BYOK credential (UserCredential) -- if a user's
selected model belongs to one of those providers but they have no stored
credential for it, generation falls back to the platform's Groq default
rather than failing the whole chat turn.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.models import Provider, ProviderModel, User
from app.service.credential_service import CredentialService
from app.service.provider_catalog_service import ProviderCatalogService
from app.utils.encryption import decrypt_secret


class ResolvedModel:
    def __init__(self, *, provider_name: str, model_name: str, api_key: str) -> None:
        self.provider_name = provider_name
        self.model_name = model_name
        self.api_key = api_key


class LLMClientService:
    @staticmethod
    async def resolve_for_user(db: AsyncSession, user: User) -> ResolvedModel:
        default_model = await ProviderCatalogService.get_default(db)
        model_id = user.model_id or default_model.id
        model = await db.get(ProviderModel, model_id) or default_model
        provider = await db.get(Provider, model.provider_id)

        if provider.name == "groq":
            return ResolvedModel(
                provider_name="groq",
                model_name=model.model_name,
                api_key=get_settings().TRENCH_CONFIG.GROQ.api_key,
            )

        byok_type = CredentialService.required_credential_type(provider.name)
        credentials = (
            await CredentialService.list_credentials(db, user_id=user.id)
            if byok_type
            else []
        )
        matching = next((c for c in credentials if c.provider_type == byok_type), None)
        if matching is not None:
            return ResolvedModel(
                provider_name=provider.name,
                model_name=model.model_name,
                api_key=decrypt_secret(matching.encrypted_credential, purpose="byok"),
            )

        # No BYOK credential for a non-Groq selection -- fall back to the
        # platform default rather than failing the chat turn.
        return ResolvedModel(
            provider_name="groq",
            model_name=default_model.model_name,
            api_key=get_settings().TRENCH_CONFIG.GROQ.api_key,
        )

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
