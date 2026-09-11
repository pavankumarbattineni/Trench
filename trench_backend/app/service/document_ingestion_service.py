"""The document ingestion pipeline: parse -> chunk -> embed -> index.

`process` is spawned as a background asyncio task right after upload (see
DocumentService.upload_document) but is plain testable async code on its
own -- no background-task machinery is needed to test it.

There is no Postgres chunk table: each chunk's text and dense+sparse
vectors go straight to Pinecone, keyed by a fresh id and namespaced by the
document's knowledge scope (personal:{user_id} / company:{organization_id}).
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Document, UsageCounter
from app.service.chunking_service import ChunkingService
from app.service.document_storage_service import get_storage_provider
from app.service.embedding_service import EmbeddingService
from app.service.parsing_service import ParsingService
from app.service.sparse_encoding_service import SparseEncodingService
from app.service.vector_store_service import (
    VectorRecord,
    company_namespace,
    get_vector_store,
    personal_namespace,
)

logger = logging.getLogger(__name__)


class DocumentIngestionService:
    @staticmethod
    async def process(db: AsyncSession, document_id: uuid.UUID) -> None:
        result = await db.execute(select(Document).where(Document.id == document_id))
        document = result.scalar_one_or_none()
        if document is None:
            return  # deleted before the task got to it -- nothing to do

        logger.info(
            "Document ingestion started | document_id=%s name=%s",
            document.id,
            document.document_name,
        )

        try:
            document.status = "processing"
            await db.commit()

            raw_bytes = await get_storage_provider().read(document.storage_path)
            text = await ParsingService.parse(
                raw_bytes, document.mime_type, filename=document.document_name
            )
            pieces = ChunkingService.chunk(text)

            dense_vectors = await EmbeddingService.embed_texts(pieces)
            sparse_vectors = SparseEncodingService.encode_documents(pieces)

            if pieces:
                namespace = (
                    company_namespace(document.organization_id)
                    if document.knowledge_type == "company"
                    else personal_namespace(document.user_id)
                )
                vector_store = get_vector_store(dimensions=EmbeddingService.DIMENSIONS)
                await vector_store.upsert(
                    namespace=namespace,
                    records=[
                        VectorRecord(
                            # Deterministic (document_id, index) id rather
                            # than a random one -- lets a later delete
                            # reconstruct exactly which Pinecone ids belong
                            # to this document from chunk_count alone, with
                            # no separate Postgres table tracking them.
                            chunk_id=f"{document.id}:{index}",
                            dense_vector=dense_vector,
                            sparse_vector=sparse_vector,
                            metadata={
                                "document_id": str(document.id),
                                "document_name": document.document_name,
                                "chunk_index": index,
                                "text": piece,
                            },
                        )
                        for index, (piece, dense_vector, sparse_vector) in enumerate(
                            zip(pieces, dense_vectors, sparse_vectors, strict=True)
                        )
                    ],
                )

            document.chunk_count = len(pieces)
            document.status = "completed"
            document.processed_at = datetime.now(UTC)
            await db.commit()
            logger.info(
                "Document ingestion completed | document_id=%s chunks=%d",
                document.id,
                len(pieces),
            )

            if document.knowledge_type == "personal":
                await DocumentIngestionService._increment_usage(db, document.user_id)
        except Exception as exc:
            logger.exception(
                "Document ingestion failed | document_id=%s", document_id
            )
            # The flush above may have already broken this session (e.g. a
            # StaleDataError because the row was deleted mid-ingestion, via
            # a concurrent delete or a cascading account deletion) -- roll
            # back before touching it again, and re-fetch rather than reuse
            # the possibly-stale `document` instance. If the row is truly
            # gone there's nothing left to mark "failed".
            await db.rollback()
            result = await db.execute(
                select(Document).where(Document.id == document_id)
            )
            document = result.scalar_one_or_none()
            if document is not None:
                document.status = "failed"
                document.error_message = str(exc)
                await db.commit()
            # This runs as a fire-and-forget asyncio.create_task (see
            # DocumentService._run_ingestion) with nothing awaiting its
            # result, so re-raising here would only surface as an
            # unretrievable "Task exception was never retrieved" warning --
            # logging above is the only way this failure is observable.

    @staticmethod
    async def _increment_usage(db: AsyncSession, user_id: uuid.UUID) -> None:
        result = await db.execute(
            select(UsageCounter).where(UsageCounter.user_id == user_id)
        )
        counter = result.scalar_one_or_none()
        if counter is None:
            counter = UsageCounter(user_id=user_id, documents_uploaded_count=1)
            db.add(counter)
        else:
            counter.documents_uploaded_count += 1
        await db.commit()
