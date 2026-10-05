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


class DocumentStatusResponse(BaseModel):
    """Just a document's processing status -- a lighter poll than the full
    list for a UI that only needs to track one document's progress (e.g.
    right after upload)."""

    model_config = ConfigDict(from_attributes=True)

    status: str


class DownloadUrlResponse(BaseModel):
    """A short-lived presigned URL straight to the storage backend -- the
    client fetches the bytes from there, never through this API."""

    url: str
    expires_in: int
