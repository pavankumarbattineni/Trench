"""Business logic for uploading, listing, fetching, and deleting documents.

Does not parse/chunk/embed/index -- this service only gets a validated
file safely stored and a `pending` Document row created (plus, for
company documents, an authorization check the caller must have already
passed at the router layer). The actual ingestion pipeline runs as a
background asyncio task (DocumentIngestionService), the same pattern
ChatService uses for chat generation -- no separate job-queue process.
"""

import asyncio
import hashlib
import logging
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Document, UsageCounter, User
from app.database.session import async_session_factory
from app.service.credential_service import CredentialService
from app.service.document_storage_service import get_storage_provider
from app.service.embedding_service import EmbeddingService
from app.service.knowledge_access_service import KnowledgeAccessService
from app.service.vector_store_service import (
    company_namespace,
    get_vector_store,
    personal_namespace,
)
from app.utils.file_validation import document_type_for, validate_upload

logger = logging.getLogger(__name__)

_ACCESS_DENIED = HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
_ALREADY_PROCESSING = HTTPException(
    status.HTTP_409_CONFLICT,
    "You already have a document being processed. Wait for it to finish "
    "before uploading another.",
)
_STILL_PROCESSING = HTTPException(
    status.HTTP_409_CONFLICT,
    "This document is still being processed. Wait for it to finish before "
    "deleting it.",
)

_ACTIVE_STATUSES = ("pending", "processing")


class DocumentService:
    FREE_DOCUMENT_LIMIT = 5

    @classmethod
    async def upload_document(
        cls,
        db: AsyncSession,
        *,
        user: User,
        filename: str,
        content: bytes,
        knowledge_type: str = "personal",
        organization_id: uuid.UUID | None = None,
    ) -> Document:
        """Validates, dedupes, enforces the free-tier limit (personal only)
        and the one-active-upload-at-a-time rule, stores, and records a new
        document.

        Args:
            user: The authenticated uploader (for company documents, an
                org admin or a member granted document-management access
                -- authorization for that must already have been checked
                by the caller).
            filename: Original filename (used only for extension-based
                disambiguation and display -- content is what's trusted).
            content: The raw file bytes.
            knowledge_type: "personal" | "company".
            organization_id: Required (and pre-authorized) for
                knowledge_type="company"; ignored for "personal".

        Returns:
            The existing Document if this exact file (by content hash) was
            already uploaded into this scope, otherwise a newly created
            `pending` Document.

        Raises:
            HTTPException: 422 on an invalid file; 403 if the free-tier
                limit is reached and the user has no Pinecone BYOK
                credential; 409 if the user already has a document
                pending/processing.
        """
        mime_type = validate_upload(filename, content)
        content_hash = hashlib.sha256(content).hexdigest()

        existing_document = await cls._find_duplicate(
            db,
            user_id=user.id,
            organization_id=organization_id,
            knowledge_type=knowledge_type,
            content_hash=content_hash,
        )
        if existing_document is not None:
            return existing_document

        await cls._enforce_no_concurrent_processing(db, user_id=user.id)
        if knowledge_type == "personal":
            await cls._enforce_free_tier_limit(db, user_id=user.id)

        document_id = uuid.uuid4()
        owner_scope = str(organization_id) if organization_id else str(user.id)
        storage_path = f"{knowledge_type}/{owner_scope}/{document_id}/{filename}"
        await get_storage_provider().save(storage_path, content)

        document = Document(
            id=document_id,
            user_id=user.id,
            organization_id=organization_id,
            document_name=filename,
            document_type=document_type_for(mime_type),
            mime_type=mime_type,
            storage_path=storage_path,
            file_size=len(content),
            content_hash=content_hash,
            knowledge_type=knowledge_type,
            knowledge_base="default" if knowledge_type == "company" else "own",
        )
        db.add(document)
        await db.commit()
        await db.refresh(document)
        logger.info(
            "Document uploaded | document_id=%s name=%s knowledge_type=%s",
            document.id,
            document.document_name,
            document.knowledge_type,
        )

        asyncio.create_task(DocumentService._run_ingestion(document.id))
        return document

    @staticmethod
    async def _run_ingestion(document_id: uuid.UUID) -> None:
        from app.service.document_ingestion_service import DocumentIngestionService

        async with async_session_factory() as session:
            await DocumentIngestionService.process(session, document_id)

    @staticmethod
    async def _find_duplicate(
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        organization_id: uuid.UUID | None,
        knowledge_type: str,
        content_hash: str,
    ) -> Document | None:
        if knowledge_type == "company":
            query = select(Document).where(
                Document.organization_id == organization_id,
                Document.content_hash == content_hash,
            )
        else:
            query = select(Document).where(
                Document.user_id == user_id, Document.content_hash == content_hash
            )
        result = await db.execute(query)
        return result.scalar_one_or_none()

    @staticmethod
    async def _enforce_no_concurrent_processing(
        db: AsyncSession, *, user_id: uuid.UUID
    ) -> None:
        result = await db.execute(
            select(Document.id).where(
                Document.user_id == user_id, Document.status.in_(_ACTIVE_STATUSES)
            )
        )
        if result.scalar_one_or_none() is not None:
            raise _ALREADY_PROCESSING

    @classmethod
    async def _enforce_free_tier_limit(
        cls, db: AsyncSession, *, user_id: uuid.UUID
    ) -> None:
        result = await db.execute(
            select(UsageCounter).where(UsageCounter.user_id == user_id)
        )
        counter = result.scalar_one_or_none()
        current_count = counter.documents_uploaded_count if counter is not None else 0

        if current_count < cls.FREE_DOCUMENT_LIMIT:
            return

        has_own_pinecone = await CredentialService.has_credential(
            db, user_id=user_id, provider_type="pinecone"
        )
        if not has_own_pinecone:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "You've used all 5 free documents. Add your own Pinecone "
                "credentials in Settings to upload more.",
            )

    @staticmethod
    async def list_personal_documents(
        db: AsyncSession, *, user_id: uuid.UUID
    ) -> list[Document]:
        result = await db.execute(
            select(Document).where(
                Document.user_id == user_id, Document.knowledge_type == "personal"
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_company_documents(
        db: AsyncSession, *, organization_id: uuid.UUID
    ) -> list[Document]:
        result = await db.execute(
            select(Document).where(
                Document.organization_id == organization_id,
                Document.knowledge_type == "company",
            )
        )
        return list(result.scalars().all())

    @classmethod
    async def get_document(
        cls, db: AsyncSession, *, user: User, document_id: uuid.UUID
    ) -> Document:
        """Fetches a document, enforcing personal-owner-only or
        company-access-grant-only visibility.

        Raises:
            HTTPException: 404 if the document doesn't exist, or the
                requester isn't authorized to see it (indistinguishable on
                purpose -- existence of a document the requester can't
                access shouldn't be observable).
        """
        result = await db.execute(select(Document).where(Document.id == document_id))
        document = result.scalar_one_or_none()
        if document is None:
            raise _ACCESS_DENIED

        if document.knowledge_type == "personal":
            if document.user_id != user.id:
                raise _ACCESS_DENIED
            return document

        authorized_org_id = (
            await KnowledgeAccessService.authorized_company_organization_id(
                db, user_id=user.id
            )
        )
        if authorized_org_id is None or authorized_org_id != document.organization_id:
            raise _ACCESS_DENIED
        return document

    @classmethod
    async def delete_document(
        cls, db: AsyncSession, *, user: User, document_id: uuid.UUID
    ) -> None:
        """Deletes a personal document the requester owns.

        Raises:
            HTTPException: 404 if the document doesn't exist, isn't
                personal, or isn't owned by `user`; 409 if the document is
                still pending/processing -- deleting it out from under the
                background ingestion task would have it fail a mid-flight
                UPDATE against a row that no longer exists.
        """
        document = await cls.get_document(db, user=user, document_id=document_id)
        await cls._delete_document_row(db, document)

    @classmethod
    async def get_company_document(
        cls, db: AsyncSession, *, organization_id: uuid.UUID, document_id: uuid.UUID
    ) -> Document:
        """Fetches a company document scoped to `organization_id`.

        Callers must already have been authorized (by the router, e.g. via
        `require_org_document_manager`) to manage this organization's
        documents -- this only verifies the document actually belongs to
        it, it doesn't re-derive per-user knowledge-access authorization
        (that's a separate, narrower grant -- see KnowledgeAccessService --
        not required to manage documents).

        Raises:
            HTTPException: 404 if the document doesn't exist, isn't a
                company document, or belongs to a different organization.
        """
        result = await db.execute(select(Document).where(Document.id == document_id))
        document = result.scalar_one_or_none()
        if (
            document is None
            or document.knowledge_type != "company"
            or document.organization_id != organization_id
        ):
            raise _ACCESS_DENIED
        return document

    @classmethod
    async def delete_company_document(
        cls, db: AsyncSession, *, organization_id: uuid.UUID, document_id: uuid.UUID
    ) -> None:
        """Deletes a company document, scoped to `organization_id`.

        Raises:
            HTTPException: 404 if the document doesn't exist or belongs to
                a different organization; 409 if it's still
                pending/processing.
        """
        document = await cls.get_company_document(
            db, organization_id=organization_id, document_id=document_id
        )
        await cls._delete_document_row(db, document)

    @staticmethod
    async def _delete_document_row(db: AsyncSession, document: Document) -> None:
        """Shared deletion mechanics for both scopes: refuses to delete a
        document still being ingested, cleans up its vectors (if any were
        indexed), removes the stored file, then the row.

        Never decrements the user's free-tier upload count -- the limit
        represents total documents ever processed, not currently existing.
        """
        if document.status in _ACTIVE_STATUSES:
            raise _STILL_PROCESSING

        if document.chunk_count > 0:
            namespace = (
                company_namespace(document.organization_id)
                if document.knowledge_type == "company"
                else personal_namespace(document.user_id)
            )
            # Chunk ids are deterministic (document_id:index), so they can
            # be reconstructed here without any Postgres record of them.
            chunk_ids = [
                f"{document.id}:{index}" for index in range(document.chunk_count)
            ]
            vector_store = get_vector_store(dimensions=EmbeddingService.DIMENSIONS)
            await vector_store.delete(namespace=namespace, ids=chunk_ids)

        await get_storage_provider().delete(document.storage_path)
        await db.delete(document)
        await db.commit()
