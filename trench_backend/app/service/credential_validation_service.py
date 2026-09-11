"""Validates a BYOK provider key actually works, before we ever store it.

Uses each provider's cheapest real auth check -- a models-list call --
matching the pattern already proven in a reference production codebase.
LangChain is used later, only to build the chat model for real generation
(see the LangGraph step), not for validation: no provider's chat-model
wrapper exposes a uniform cheap "is this key valid" check, but every
provider's native SDK does via listing models.
"""

from fastapi import HTTPException, status

_INVALID_KEY_MESSAGES = {
    "openai_llm": "That OpenAI API key looks invalid or expired.",
    "anthropic_llm": "That Anthropic API key looks invalid or expired.",
    "gemini_llm": "That Google Gemini API key looks invalid or expired.",
}


class CredentialValidationService:
    """One validator per LLM provider_type. The "pinecone" provider_type
    (a BYOK vector-store project) isn't validated yet -- added when that
    path is wired up."""

    @classmethod
    async def validate(cls, provider_type: str, api_key: str) -> None:
        validator = getattr(cls, f"_validate_{provider_type}", None)
        if validator is None:
            return
        await validator(api_key)

    @staticmethod
    async def _validate_openai_llm(api_key: str) -> None:
        from openai import AsyncOpenAI

        try:
            await AsyncOpenAI(api_key=api_key).models.list()
        except Exception as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                _INVALID_KEY_MESSAGES["openai_llm"],
            ) from exc

    @staticmethod
    async def _validate_anthropic_llm(api_key: str) -> None:
        from anthropic import AsyncAnthropic

        try:
            await AsyncAnthropic(api_key=api_key).models.list()
        except Exception as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                _INVALID_KEY_MESSAGES["anthropic_llm"],
            ) from exc

    @staticmethod
    async def _validate_gemini_llm(api_key: str) -> None:
        from google import genai

        try:
            client = genai.Client(api_key=api_key)
            await client.aio.models.list()
        except Exception as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                _INVALID_KEY_MESSAGES["gemini_llm"],
            ) from exc
