"""Document upload/list/get/delete endpoints.

Only creates a `pending` Document and stores the raw file -- parsing,
chunking, embedding, and indexing happen in a separate background job.
"""

import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Document, User
from app.database.session import get_db
from app.router.deps import get_current_user
from app.schemas.document import (
    ChunkingStrategy,
    DocumentListResponse,
    DocumentResponse,
)
from app.service.document_service import DocumentService

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", response_model=DocumentResponse)
async def upload_document(
    file: UploadFile = File(...),
    chunking_strategy: ChunkingStrategy = Form("recursive"),
    chunk_size: int = Form(512, ge=100, le=4000),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Document:
    """Uploads a document for processing.

    Args:
        file: The document file (PDF, DOCX, TXT, or Markdown).
        chunking_strategy: One of "recursive" | "markdown" | "semantic".
        chunk_size: Target chunk size in tokens (100-4000).

    Returns:
        The created (or, if this exact file was already uploaded, the
        existing) Document.

    Raises:
        HTTPException: 422 on an invalid file; 403 if the free-tier
            document limit is reached and no Pinecone BYOK credential is set.
    """
    content = await file.read()
    return await DocumentService.upload_document(
        db,
        user=current_user,
        filename=file.filename or "untitled",
        content=content,
        chunking_strategy=chunking_strategy,
        chunk_size=chunk_size,
    )


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentListResponse:
    """Lists the authenticated user's documents."""
    documents = await DocumentService.list_documents(db, user_id=current_user.id)
    return DocumentListResponse(documents=documents)


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Document:
    """Fetches a single document owned by the authenticated user."""
    return await DocumentService.get_document(
        db, user_id=current_user.id, document_id=document_id
    )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Deletes a document owned by the authenticated user.

    Never decrements the user's free-tier upload count -- the limit
    represents total documents ever processed, not currently existing ones.
    """
    await DocumentService.delete_document(
        db, user_id=current_user.id, document_id=document_id
    )
