import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

ChunkingStrategy = Literal["recursive", "markdown", "semantic"]


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    mime_type: str
    file_size_bytes: int
    status: str
    error_message: str | None
    chunking_strategy: str
    chunk_size: int
    chunk_count: int
    created_at: datetime
    processed_at: datetime | None


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
