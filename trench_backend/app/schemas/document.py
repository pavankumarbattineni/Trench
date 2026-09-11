import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_name: str
    document_type: str
    knowledge_type: str
    knowledge_base: str
    file_size: int
    status: str
    error_message: str | None
    chunk_count: int
    created_at: datetime
    processed_at: datetime | None


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
