"""The document ingestion pipeline: parse -> chunk -> embed -> index.

`process` is spawned as a background asyncio task right after upload (see
DocumentService.upload_document) but is plain testable async code on its
own -- no background-task machinery is needed to test it.

There is no Postgres chunk table: each chunk's text and dense+sparse
vectors go straight to Pinecone, keyed by a fresh id and namespaced by the
document's knowledge scope (personal:{user_id} / company:{tenant_id}).
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Document, User
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

            # Reuses a prior successful parse if one is cached (see
            # Document.parsed_text's own docstring) -- a retry after a
            # chunking/embedding/indexing failure shouldn't re-download
            # from storage or re-run LlamaParse, both real per-call costs,
            # for a document whose text was already correctly extracted.
            if document.parsed_text is not None:
                text = document.parsed_text
            else:
                # A storage failure raises DocumentStorageError, whose
                # message is written for the user ("Failed to read the
                # document from storage.") -- it's handled by the same
                # catch-all below as any parse/embed/index failure,
                # landing as status="failed" with that message as
                # error_message.
                raw_bytes = await get_storage_provider().read(document.storage_path)
                text = await ParsingService.parse(
                    raw_bytes, document.mime_type, filename=document.document_name
                )
                document.parsed_text = text
                await db.commit()
            pieces = ChunkingService.chunk(text)
            # The embedded/sparse-encoded text is contextualized with the
            # document name and heading path (e.g. "Refunds > Timelines")
            # so retrieval can match a query against context a bare chunk
            # wouldn't carry on its own; the Pinecone "text" metadata (what
            # generate()/citations actually show the model/user) stays the
            # raw chunk text, never this prefixed version.
            embed_texts = [
                "\n".join(
                    line
                    for line in (document.document_name, piece.heading_path)
                    if line
                )
                + "\n\n"
                + piece.text
                for piece in pieces
            ]

            dense_vectors = await EmbeddingService.embed_texts(embed_texts)
            sparse_vectors = SparseEncodingService.encode_documents(embed_texts)

            if pieces:
                namespace = (
                    company_namespace(document.tenant_id)
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
                                "text": piece.text,
                                "heading_path": piece.heading_path,
                                "index_version": 2,
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
            # Nothing reads parsed_text once a document has successfully
            # completed -- it only exists to let a retry skip re-parsing
            # after a chunking/embedding/indexing failure (see its own
            # docstring). Clearing it here, in the same transaction as the
            # completion itself, avoids permanently duplicating every
            # document's full text in Postgres on top of what's already
            # in Pinecone's chunk metadata.
            document.parsed_text = None
            await db.commit()
            logger.info(
                "Document ingestion completed | document_id=%s chunks=%d",
                document.id,
                len(pieces),
            )

            if document.knowledge_type == "personal":
                await DocumentIngestionService._increment_usage(db, document.user_id)
        except Exception as exc:
            logger.exception("Document ingestion failed | document_id=%s", document_id)
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
        # A single atomic `count = count + 1` UPDATE rather than a
        # read-modify-write on a loaded User, so concurrent ingestions for
        # the same user can't lose an increment.
        await db.execute(
            update(User)
            .where(User.id == user_id)
            .values(documents_uploaded_count=User.documents_uploaded_count + 1)
            .execution_options(synchronize_session="fetch")
        )
        await db.commit()
