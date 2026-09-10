"""Business logic for uploading, listing, fetching, and deleting documents.

Does not parse/chunk/embed/index -- this service only gets a validated
file safely stored and a `pending` Document row created. The actual
ingestion pipeline is a separate background job.
"""

import hashlib
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Document, UsageCounter, User
from app.service.credential_service import CredentialService
from app.service.document_storage_service import get_storage_provider
from app.service.knowledge_base_service import KnowledgeBaseService
from app.utils.file_validation import validate_upload


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
        chunking_strategy: str,
        chunk_size: int,
    ) -> Document:
        """Validates, dedupes, enforces the free-tier limit, stores, and
        records a new document.

        Args:
            user: The authenticated uploader.
            filename: Original filename (used for extension-based
                disambiguation only -- content is what's actually trusted).
            content: The raw file bytes.
            chunking_strategy: One of "recursive" | "markdown" | "semantic".
            chunk_size: Target chunk size in tokens.

        Returns:
            The existing Document if this exact file (by content hash) was
            already uploaded by this user, otherwise a newly created
            `pending` Document.

        Raises:
            HTTPException: 422 on an invalid file; 403 if the free-tier
                limit is reached and the user has no Pinecone BYOK credential.
        """
        mime_type = validate_upload(filename, content)
        content_hash = hashlib.sha256(content).hexdigest()

        existing = await db.execute(
            select(Document).where(
                Document.user_id == user.id, Document.content_hash == content_hash
            )
        )
        existing_document = existing.scalar_one_or_none()
        if existing_document is not None:
            return existing_document

        await cls._enforce_free_tier_limit(db, user_id=user.id)

        knowledge_base = await KnowledgeBaseService.get_or_create_default(db, user.id)

        document_id = uuid.uuid4()
        storage_path = f"{user.id}/{document_id}/{filename}"
        await get_storage_provider().save(storage_path, content)

        document = Document(
            id=document_id,
            user_id=user.id,
            knowledge_base_id=knowledge_base.id,
            filename=filename,
            storage_path=storage_path,
            mime_type=mime_type,
            file_size_bytes=len(content),
            content_hash=content_hash,
            chunking_strategy=chunking_strategy,
            chunk_size=chunk_size,
        )
        db.add(document)
        await db.commit()
        await db.refresh(document)
        return document

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
    async def list_documents(db: AsyncSession, *, user_id: uuid.UUID) -> list[Document]:
        result = await db.execute(select(Document).where(Document.user_id == user_id))
        return list(result.scalars().all())

    @staticmethod
    async def get_document(
        db: AsyncSession, *, user_id: uuid.UUID, document_id: uuid.UUID
    ) -> Document:
        result = await db.execute(
            select(Document).where(
                Document.id == document_id, Document.user_id == user_id
            )
        )
        document = result.scalar_one_or_none()
        if document is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
        return document

    @classmethod
    async def delete_document(
        cls, db: AsyncSession, *, user_id: uuid.UUID, document_id: uuid.UUID
    ) -> None:
        document = await cls.get_document(db, user_id=user_id, document_id=document_id)
        # TODO(Step 5/6): also purge this document's vectors from Pinecone
        # once indexing exists -- nothing is indexed yet at this point in
        # the build, so there's nothing to purge today.
        await get_storage_provider().delete(document.storage_path)
        await db.delete(document)
        await db.commit()
