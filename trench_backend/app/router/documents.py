"""Personal-knowledge document upload/list/get/delete endpoints.

Only creates a `pending` Document and stores the raw file -- parsing,
chunking, embedding, and indexing happen in a background asyncio task
(see DocumentService.upload_document). Company-knowledge documents are
managed under /organizations instead (see app/router/organizations.py)
since they require admin authorization.
"""

import uuid

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Document, User
from app.database.session import get_db
from app.router.deps import get_current_user
from app.schemas.document import DocumentListResponse, DocumentResponse
from app.service.document_service import DocumentService

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", response_model=DocumentResponse)
async def upload_document(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Document:
    """Uploads a personal-knowledge document for processing.

    Args:
        file: The document file (PDF, DOCX, TXT, or Markdown).
        current_user: The authenticated uploader; the document is scoped
            to this user's personal knowledge base.
        db: An active async SQLAlchemy session.

    Returns:
        The created (or, if this exact file was already uploaded, the
        existing) Document.

    Raises:
        HTTPException: 422 on an invalid file; 403 if the free-tier
            document limit is reached and no Pinecone BYOK credential is
            set; 409 if the user already has a document pending/processing.
    """
    content = await file.read()
    return await DocumentService.upload_document(
        db,
        user=current_user,
        filename=file.filename or "untitled",
        content=content,
        knowledge_type="personal",
    )


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentListResponse:
    """Lists the authenticated user's personal documents.

    Args:
        current_user: The authenticated user whose documents to list.
        db: An active async SQLAlchemy session.

    Returns:
        Every personal document owned by the authenticated user.
    """
    documents = await DocumentService.list_personal_documents(
        db, user_id=current_user.id
    )
    return DocumentListResponse(documents=documents)


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Document:
    """Fetches a single document the authenticated user is authorized to see.

    Args:
        document_id: The document to fetch.
        current_user: The authenticated user requesting it.
        db: An active async SQLAlchemy session.

    Returns:
        The requested document.

    Raises:
        HTTPException: 404 if the document doesn't exist or isn't owned by
            the requester.
    """
    return await DocumentService.get_document(
        db, user=current_user, document_id=document_id
    )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Deletes a personal document owned by the authenticated user.

    Never decrements the user's free-tier upload count -- the limit
    represents total documents ever processed, not currently existing ones.

    Args:
        document_id: The document to delete.
        current_user: The authenticated user requesting deletion.
        db: An active async SQLAlchemy session.

    Raises:
        HTTPException: 404 if the document doesn't exist or isn't owned by
            the requester.
    """
    await DocumentService.delete_document(
        db, user=current_user, document_id=document_id
    )
