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
from sqlalchemy.orm import InstrumentedAttribute

from app.database.models import Document, User
from app.database.session import async_session_factory
from app.service.credential_service import CredentialService
from app.service.document_storage_service import (
    Disposition,
    DocumentStorageError,
    get_storage_provider,
)
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

# How long a presigned download URL stays valid. Short on purpose: the
# frontend fetches a fresh one per click, and a leaked URL grants access
# to the bytes with no further authorization check.
DOWNLOAD_URL_EXPIRES_IN = 300


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
        tenant_id: uuid.UUID | None = None,
    ) -> Document:
        """Validates, dedupes, enforces the free-tier limit (personal only)
        and the one-active-upload-at-a-time rule, stores, and records a new
        document.

        Args:
            user: The authenticated uploader (for company documents, a
                tenant Admin/Owner -- authorization for that must already
                have been checked by the caller).
            filename: Original filename (used only for extension-based
                disambiguation and display -- content is what's trusted).
            content: The raw file bytes.
            knowledge_type: "personal" | "company".
            tenant_id: Required (and pre-authorized) for
                knowledge_type="company"; ignored for "personal".

        Returns:
            The existing Document if this exact file (by content hash) was
            already uploaded into this scope, otherwise a newly created
            `pending` Document.

        Raises:
            HTTPException: 422 on an invalid file; 403 if the free-tier
                limit is reached (a personal Pinecone BYOK credential lifts
                it, but that provider_type can no longer be added via the
                API -- see VALID_PROVIDER_TYPES -- so this only still
                applies to a pre-existing credential row); 409 if the user
                already has a document pending/processing; 502 if the
                storage backend rejects the write.
        """
        mime_type = validate_upload(filename, content)
        content_hash = hashlib.sha256(content).hexdigest()

        existing_document = await cls._find_duplicate(
            db,
            user_id=user.id,
            tenant_id=tenant_id,
            knowledge_type=knowledge_type,
            content_hash=content_hash,
        )
        if existing_document is not None:
            return existing_document

        await cls._enforce_no_concurrent_processing(db, user_id=user.id)
        if knowledge_type == "personal":
            await cls._enforce_free_tier_limit(db, user_id=user.id)

        document_id = uuid.uuid4()
        owner_scope = str(tenant_id) if tenant_id else str(user.id)
        storage_path = f"{knowledge_type}/{owner_scope}/{document_id}/{filename}"
        # Bytes are stored *before* the row is created (and nothing has been
        # added to the session yet), so a storage failure can never leave a
        # `pending` Document with no bytes behind it for ingestion to choke
        # on. The reverse failure -- object written, commit fails -- leaves
        # at most one unreferenced object in the bucket, which is harmless.
        try:
            await get_storage_provider().save(storage_path, content)
        except DocumentStorageError as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "Failed to store the uploaded document. Please try again.",
            ) from exc

        document = Document(
            id=document_id,
            user_id=user.id,
            tenant_id=tenant_id,
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
        tenant_id: uuid.UUID | None,
        knowledge_type: str,
        content_hash: str,
    ) -> Document | None:
        if knowledge_type == "company":
            query = select(Document).where(
                Document.tenant_id == tenant_id,
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
        # Read straight from the column rather than off a possibly stale
        # in-memory User -- the count is incremented by the background
        # ingestion task in its own session.
        result = await db.execute(
            select(User.documents_uploaded_count).where(User.id == user_id)
        )
        current_count = result.scalar_one_or_none() or 0

        if current_count < cls.FREE_DOCUMENT_LIMIT:
            return

        has_own_pinecone = await CredentialService.has_credential(
            db, user_id=user_id, provider_type="pinecone"
        )
        if not has_own_pinecone:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "You've reached the 5-document free limit.",
            )

    @staticmethod
    async def _list_documents(
        db: AsyncSession,
        *,
        knowledge_type: str,
        owner_column: InstrumentedAttribute[uuid.UUID],
        owner_id: uuid.UUID,
    ) -> list[Document]:
        result = await db.execute(
            select(Document).where(
                owner_column == owner_id, Document.knowledge_type == knowledge_type
            )
        )
        return list(result.scalars().all())

    @classmethod
    async def list_personal_documents(
        cls, db: AsyncSession, *, user_id: uuid.UUID
    ) -> list[Document]:
        return await cls._list_documents(
            db,
            knowledge_type="personal",
            owner_column=Document.user_id,
            owner_id=user_id,
        )

    @classmethod
    async def list_company_documents(
        cls, db: AsyncSession, *, tenant_id: uuid.UUID
    ) -> list[Document]:
        return await cls._list_documents(
            db,
            knowledge_type="company",
            owner_column=Document.tenant_id,
            owner_id=tenant_id,
        )

    @staticmethod
    async def _has_any_documents(
        db: AsyncSession,
        *,
        knowledge_type: str,
        owner_column: InstrumentedAttribute[uuid.UUID],
        owner_id: uuid.UUID,
    ) -> bool:
        """A LIMIT 1 existence check, not a count -- cheaper, and every
        caller only needs a boolean."""
        result = await db.execute(
            select(Document.id)
            .where(
                owner_column == owner_id,
                Document.knowledge_type == knowledge_type,
                Document.status == "completed",
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    @classmethod
    async def has_any_personal_documents(
        cls, db: AsyncSession, *, user_id: uuid.UUID
    ) -> bool:
        """Whether `user_id` has at least one fully-ingested personal
        document -- used by the RAG graph to tell "this knowledge base has
        never had anything in it" apart from "nothing relevant matched
        this query" (see app/graph/rag_graph.py's validate_knowledge_access)."""
        return await cls._has_any_documents(
            db,
            knowledge_type="personal",
            owner_column=Document.user_id,
            owner_id=user_id,
        )

    @classmethod
    async def has_any_company_documents(
        cls, db: AsyncSession, *, tenant_id: uuid.UUID
    ) -> bool:
        """Same as has_any_personal_documents, scoped to a tenant's
        company knowledge instead."""
        return await cls._has_any_documents(
            db,
            knowledge_type="company",
            owner_column=Document.tenant_id,
            owner_id=tenant_id,
        )

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

        authorized_id = await KnowledgeAccessService.authorized_company_tenant_id(
            db, user_id=user.id
        )
        if authorized_id is None or authorized_id != document.tenant_id:
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
        cls, db: AsyncSession, *, tenant_id: uuid.UUID, document_id: uuid.UUID
    ) -> Document:
        """Fetches a company document scoped to `tenant_id`.

        Callers must already have been authorized (by the router, via
        `require_tenant_admin_or_owner`) to manage this tenant's documents
        -- this only verifies the document actually belongs to it, it
        doesn't re-derive per-user knowledge-access authorization (that's
        a separate, narrower grant -- see KnowledgeAccessService -- not
        required to manage documents).

        Raises:
            HTTPException: 404 if the document doesn't exist, isn't a
                company document, or belongs to a different tenant.
        """
        result = await db.execute(select(Document).where(Document.id == document_id))
        document = result.scalar_one_or_none()
        if (
            document is None
            or document.knowledge_type != "company"
            or document.tenant_id != tenant_id
        ):
            raise _ACCESS_DENIED
        return document

    @classmethod
    async def delete_company_document(
        cls, db: AsyncSession, *, tenant_id: uuid.UUID, document_id: uuid.UUID
    ) -> None:
        """Deletes a company document, scoped to `tenant_id`.

        Raises:
            HTTPException: 404 if the document doesn't exist or belongs to
                a different tenant; 409 if it's still pending/processing.
        """
        document = await cls.get_company_document(
            db, tenant_id=tenant_id, document_id=document_id
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
                company_namespace(document.tenant_id)
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

        # A failed storage delete deliberately does NOT block the DB delete:
        # one orphaned object left in the bucket is a far smaller problem
        # than a document the user can never get rid of (and every retry
        # would hit the same failure). The object key is logged so it can
        # be cleaned up by hand; the user still sees a successful delete.
        try:
            await get_storage_provider().delete(document.storage_path)
        except DocumentStorageError:
            logger.error(
                "Orphaned storage object after document delete | "
                "document_id=%s storage_path=%s",
                document.id,
                document.storage_path,
            )
        await db.delete(document)
        await db.commit()

    @classmethod
    async def generate_download_url(
        cls,
        db: AsyncSession,
        *,
        user: User,
        document_id: uuid.UUID,
        disposition: Disposition,
    ) -> str:
        """Returns a short-lived presigned URL to the document's bytes,
        after the same visibility check as get_document.

        Raises:
            HTTPException: 404 if the document doesn't exist or the
                requester isn't authorized to see it; 502 if the storage
                backend can't sign the URL.
        """
        document = await cls.get_document(db, user=user, document_id=document_id)
        return await cls._presigned_url_for(document, disposition)

    @classmethod
    async def generate_company_download_url(
        cls,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        document_id: uuid.UUID,
        disposition: Disposition,
    ) -> str:
        """Returns a short-lived presigned URL to a company document's
        bytes. Like get_company_document, this only verifies the document
        belongs to `tenant_id` -- the caller's right to read that tenant's
        company knowledge must already have been checked by the router.

        Raises:
            HTTPException: 404 if the document doesn't exist or belongs to
                a different tenant; 502 if the storage backend can't sign
                the URL.
        """
        document = await cls.get_company_document(
            db, tenant_id=tenant_id, document_id=document_id
        )
        return await cls._presigned_url_for(document, disposition)

    @staticmethod
    async def _presigned_url_for(document: Document, disposition: Disposition) -> str:
        try:
            return await get_storage_provider().generate_presigned_url(
                document.storage_path,
                filename=document.document_name,
                mime_type=document.mime_type,
                disposition=disposition,
                expires_in=DOWNLOAD_URL_EXPIRES_IN,
            )
        except DocumentStorageError as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                "Failed to generate a download link. Please try again.",
            ) from exc
