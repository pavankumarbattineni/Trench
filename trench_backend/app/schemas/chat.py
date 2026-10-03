from typing import Any, Literal

from pydantic import BaseModel, Field

KnowledgeType = Literal["personal", "company"]


class ChatMessageRequest(BaseModel):
    query: str = Field(min_length=1, max_length=8000)
    knowledge_type: KnowledgeType = "personal"


class ChatMessageAcceptedResponse(BaseModel):
    stream_id: str
    status: str


class StreamStatusResponse(BaseModel):
    status: str
    content: str
    chunks: list[dict[str, Any]]
