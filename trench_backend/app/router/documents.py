"""Document upload/list/download/delete -- for both personal and
company-knowledge documents.

A single set of four endpoints handles both scopes, distinguished by the
optional `tenant_id`: omitted means the caller's own personal documents
(any authenticated user may manage their own); given means that tenant's
company documents, which require the appropriate tenant-level
authorization for the operation being performed -- admin/owner for
upload/retry/download/delete, or company-knowledge access (admin/owner, or
a member explicitly granted it) for listing. Those checks reuse the same
`require_tenant_admin_or_owner`/`require_company_knowledge_access`
dependency functions the rest of app/router/tenants.py uses, just called
directly rather than injected, since `tenant_id` here is an optional
form/query value rather than always being a path segment.

There is no GET /{document_id} (fetch a full single document) -- nothing in
the frontend ever called it, and list already returns everything a client
needs to render a document row. GET /{document_id}/status exists alongside
it, though: a lighter poll returning just the processing status, for a
client tracking one specific document (e.g. right after its own upload)
without re-fetching/re-rendering the whole list on every poll tick.
"""

import uuid
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Document, User
from app.database.session import get_db
from app.router.deps import (
    get_current_user,
    require_company_knowledge_access,
    require_tenant_admin_or_owner,
)
from app.schemas.document import (
    DocumentListResponse,
    DocumentResponse,
    DocumentStatusResponse,
    DownloadUrlResponse,
)
from app.service.document_service import DOWNLOAD_URL_EXPIRES_IN, DocumentService

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", response_model=DocumentResponse)
async def upload_document(
    file: UploadFile | None = File(default=None),
    document_id: uuid.UUID | None = Form(default=None),
    retry: bool = Form(default=False),
    tenant_id: uuid.UUID | None = Form(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Document:
    """Uploads a document for processing, or (with retry=true) re-triggers
    ingestion for an existing failed one.

    Args:
        file: The document file (PDF, DOCX, TXT, or Markdown). Required
            unless retry=true, since a retry reuses the bytes already in
            storage from the original upload.
        document_id: The document to retry. Required when retry=true;
            ignored otherwise.
        retry: When true, re-triggers ingestion for `document_id` instead
            of accepting a new upload.
        tenant_id: Omitted for a personal document (scoped to
            `current_user`); given to upload/retry a company document for
            that tenant, which requires `current_user` to be an Admin or
            the Owner of it.
        current_user: The authenticated caller.
        db: An active async SQLAlchemy session.

    Returns:
        The created (or, if this exact file was already uploaded into
        this scope, the existing) Document on a normal upload; the
        retried Document (status reset to "pending") when retry=true.

    Raises:
        HTTPException: 422 if neither a file nor retry=true+document_id
            was given, or on an invalid file; 404 if `tenant_id` doesn't
            exist, or (on retry) `document_id` doesn't belong to the
            resolved scope; 403 if `tenant_id` is given and the caller
            isn't an Admin/Owner of it; 403/404 if `document_id` isn't a
            personal document owned by the caller (personal retry only);
            409 if the caller already has a document pending/processing,
            or (on retry) the document isn't currently "failed".
    """
    if tenant_id is not None:
        await require_tenant_admin_or_owner(
            tenant_id=tenant_id, current_user=current_user, db=db
        )

    if retry:
        if document_id is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "document_id is required when retry=true.",
            )
        if tenant_id is not None:
            return await DocumentService.retry_company_document(
                db, admin=current_user, tenant_id=tenant_id, document_id=document_id
            )
        return await DocumentService.retry_document(
            db, user=current_user, document_id=document_id
        )

    if file is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "A file is required unless retry=true.",
        )
    content = await file.read()
    return await DocumentService.upload_document(
        db,
        user=current_user,
        filename=file.filename or "untitled",
        content=content,
        knowledge_type="company" if tenant_id is not None else "personal",
        tenant_id=tenant_id,
    )


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    tenant_id: uuid.UUID | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentListResponse:
    """Lists documents -- the caller's personal ones, or (with
    `tenant_id`) a tenant's company ones.

    Args:
        tenant_id: Omitted to list `current_user`'s personal documents;
            given to list that tenant's company documents, which requires
            `current_user` to have company-knowledge access to it (an
            Admin/Owner, or a member explicitly granted access).
        current_user: The authenticated caller.
        db: An active async SQLAlchemy session.

    Returns:
        Every document in the resolved scope.

    Raises:
        HTTPException: 403 if `tenant_id` is given and the caller doesn't
            have company-knowledge access to it.
    """
    if tenant_id is not None:
        await require_company_knowledge_access(
            tenant_id=tenant_id, current_user=current_user, db=db
        )
        documents = await DocumentService.list_company_documents(
            db, tenant_id=tenant_id
        )
    else:
        documents = await DocumentService.list_personal_documents(
            db, user_id=current_user.id
        )
    return DocumentListResponse(documents=documents)


@router.get("/{document_id}/status", response_model=DocumentStatusResponse)
async def get_document_status(
    document_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DocumentStatusResponse:
    """Returns just a document's current processing status -- a lighter
    poll than re-fetching the full list, for a client tracking one
    specific document's progress (e.g. right after its own upload).

    Visibility matches GET /documents (list), for either scope: the owner
    for a personal document, or anyone with company-knowledge access to
    its tenant for a company one -- `tenant_id` isn't needed as a
    parameter here since DocumentService.get_document already resolves
    the right check from the document's own knowledge_type.

    Args:
        document_id: The document to check.
        current_user: The authenticated caller.
        db: An active async SQLAlchemy session.

    Returns:
        The document's current status ("pending" | "processing" |
        "completed" | "failed").

    Raises:
        HTTPException: 404 if the document doesn't exist or isn't visible
            to the caller.
    """
    document = await DocumentService.get_document(
        db, user=current_user, document_id=document_id
    )
    return DocumentStatusResponse.model_validate(document)


@router.get("/{document_id}/download", response_model=DownloadUrlResponse)
async def download_document(
    document_id: uuid.UUID,
    tenant_id: uuid.UUID | None = Query(default=None),
    disposition: Literal["inline", "attachment"] = Query("inline"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DownloadUrlResponse:
    """Returns a short-lived presigned URL to a document.

    Args:
        document_id: The document to download.
        tenant_id: Omitted for a personal document owned by
            `current_user`; given for a tenant's company document, which
            -- deliberately stricter than listing -- requires
            `current_user` to be an Admin or the Owner of it (a member's
            company-knowledge grant is a retrieval permission, not a
            document-management one).
        disposition: "inline" to view in the browser, "attachment" to
            force a download.
        current_user: The authenticated caller.
        db: An active async SQLAlchemy session.

    Returns:
        The presigned URL and its lifetime in seconds.

    Raises:
        HTTPException: 403 if `tenant_id` is given and the caller isn't an
            Admin/Owner of it; 404 if the document doesn't exist or isn't
            visible in the resolved scope; 502 if the storage backend
            can't sign the URL.
    """
    if tenant_id is not None:
        await require_tenant_admin_or_owner(
            tenant_id=tenant_id, current_user=current_user, db=db
        )
        url = await DocumentService.generate_company_download_url(
            db, tenant_id=tenant_id, document_id=document_id, disposition=disposition
        )
    else:
        url = await DocumentService.generate_download_url(
            db, user=current_user, document_id=document_id, disposition=disposition
        )
    return DownloadUrlResponse(url=url, expires_in=DOWNLOAD_URL_EXPIRES_IN)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    tenant_id: uuid.UUID | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Deletes a document.

    Never decrements the free-tier upload count (personal scope) -- the
    limit represents total documents ever processed, not currently
    existing ones.

    Args:
        document_id: The document to delete.
        tenant_id: Omitted for a personal document owned by
            `current_user`; given for a tenant's company document, which
            requires `current_user` to be an Admin or the Owner of it.
        current_user: The authenticated caller.
        db: An active async SQLAlchemy session.

    Raises:
        HTTPException: 403 if `tenant_id` is given and the caller isn't an
            Admin/Owner of it; 404 if the document doesn't exist or isn't
            visible in the resolved scope; 409 if the document is still
            pending/processing.
    """
    if tenant_id is not None:
        await require_tenant_admin_or_owner(
            tenant_id=tenant_id, current_user=current_user, db=db
        )
        await DocumentService.delete_company_document(
            db, tenant_id=tenant_id, document_id=document_id
        )
    else:
        await DocumentService.delete_document(
            db, user=current_user, document_id=document_id
        )
