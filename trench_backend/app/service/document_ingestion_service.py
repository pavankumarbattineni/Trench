"""The document ingestion pipeline.

`process` is called by the procrastinate task (app/jobs/document_tasks.py)
but is plain testable async code on its own -- no procrastinate/worker
machinery is needed to test it.

Stub for Step 4a: proves enqueue -> worker -> status transition -> usage-
counter-increment works end-to-end. Real parsing/chunking/embedding is
added in Step 4b, indexing in Step 4c -- this function's body grows then;
its signature and role don't change.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Document, UsageCounter


class DocumentIngestionService:
    @staticmethod
    async def process(db: AsyncSession, document_id: uuid.UUID) -> None:
        result = await db.execute(select(Document).where(Document.id == document_id))
        document = result.scalar_one_or_none()
        if document is None:
            return  # deleted before the worker got to it -- nothing to do

        try:
            document.status = "processing"
            await db.commit()

            # Stub: real parse/chunk/embed/index pipeline lands in 4b/4c.
            document.chunk_count = 0

            document.status = "completed"
            document.processed_at = datetime.now(UTC)
            await db.commit()

            await DocumentIngestionService._increment_usage(db, document.user_id)
        except Exception as exc:
            document.status = "failed"
            document.error_message = str(exc)
            await db.commit()
            raise

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
