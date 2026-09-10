from datetime import datetime

from pydantic import BaseModel

VALID_PROVIDER_TYPES = {
    "llamaparse",
    "openai_embed",
    "openai_llm",
    "anthropic_llm",
    "gemini_llm",
    "cohere_rerank",
    "pinecone",
}


class SaveCredentialRequest(BaseModel):
    provider_type: str
    api_key: str


class CredentialResponse(BaseModel):
    provider_type: str
    masked_preview: str
    validated_at: datetime


class CredentialListResponse(BaseModel):
    credentials: list[CredentialResponse]
