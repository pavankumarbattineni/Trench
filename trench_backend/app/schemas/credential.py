import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

VALID_PROVIDER_TYPES = {
    "openai_llm",
    "anthropic_llm",
    "gemini_llm",
    "pinecone",
}

CredentialScope = Literal["personal", "tenant"]


class SaveCredentialRequest(BaseModel):
    """POST /credentials -- tenant_id is optional: given, this saves the
    tenant's shared BYOK credential (caller must be its Owner); omitted,
    this saves the caller's own personal credential. There is no separate
    tenant-credentials API."""

    provider_type: str
    api_key: str
    tenant_id: uuid.UUID | None = None


class CredentialResponse(BaseModel):
    provider_type: str
    masked_preview: str
    validated_at: datetime
    scope: CredentialScope


class CredentialListResponse(BaseModel):
    credentials: list[CredentialResponse]
