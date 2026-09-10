import uuid

import pytest
from sqlalchemy import select

from app.database.models import Document, KnowledgeBase, UsageCounter, User
from app.database.session import async_session_factory
from app.service.document_ingestion_service import DocumentIngestionService

TEST_FIREBASE_UID_PREFIX = "test-ingestion"


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.firebase_uid.like(f"{TEST_FIREBASE_UID_PREFIX}%"))
        )
        for user in result.scalars().all():
            await session.delete(user)
        await session.commit()


async def _make_document(session, suffix: str) -> Document:
    user = User(
        firebase_uid=f"{TEST_FIREBASE_UID_PREFIX}-{suffix}",
        email=f"test-ingestion-{suffix}@example.com",
        username=f"test_ingestion_{suffix}",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    kb = KnowledgeBase(user_id=user.id)
    session.add(kb)
    await session.commit()
    await session.refresh(kb)

    document = Document(
        user_id=user.id,
        knowledge_base_id=kb.id,
        filename="notes.txt",
        storage_path="irrelevant-for-this-test",
        mime_type="text/plain",
        file_size_bytes=10,
        content_hash=f"{suffix:0<64}"[:64],
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)
    return document


@pytest.mark.asyncio
async def test_process_marks_document_completed_and_increments_usage():
    async with async_session_factory() as session:
        document = await _make_document(session, "a")

        await DocumentIngestionService.process(session, document.id)

        await session.refresh(document)
        assert document.status == "completed"
        assert document.processed_at is not None

        counter_result = await session.execute(
            select(UsageCounter).where(UsageCounter.user_id == document.user_id)
        )
        counter = counter_result.scalar_one()
        assert counter.documents_uploaded_count == 1


@pytest.mark.asyncio
async def test_process_increments_existing_usage_counter():
    async with async_session_factory() as session:
        document = await _make_document(session, "b")
        session.add(UsageCounter(user_id=document.user_id, documents_uploaded_count=3))
        await session.commit()

        await DocumentIngestionService.process(session, document.id)

        counter_result = await session.execute(
            select(UsageCounter).where(UsageCounter.user_id == document.user_id)
        )
        counter = counter_result.scalar_one()
        assert counter.documents_uploaded_count == 4


@pytest.mark.asyncio
async def test_process_is_a_noop_for_missing_document():
    async with async_session_factory() as session:
        # Should return quietly rather than raise -- the document may have
        # been deleted before the worker got to it.
        await DocumentIngestionService.process(session, uuid.uuid4())
