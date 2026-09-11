import uuid

import pytest
from sqlalchemy import select

from app.database.models import Document, UsageCounter, User
from app.database.session import async_session_factory
from app.service.document_ingestion_service import DocumentIngestionService
from app.service.document_storage_service import LocalFilesystemStorageProvider
from app.service.embedding_service import EmbeddingService

TEST_EMAIL_PREFIX = "test-ingestion"


class _FakeVectorStore:
    def __init__(self) -> None:
        self.upserted: list[tuple[str, int]] = []

    async def upsert(self, *, namespace, records) -> None:
        self.upserted.append((namespace, len(records)))

    async def delete(self, *, namespace, ids) -> None:
        pass


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.email.like(f"{TEST_EMAIL_PREFIX}%"))
        )
        for user in result.scalars().all():
            await session.delete(user)
        await session.commit()


async def _make_document(session, suffix: str, *, content_hash: str) -> Document:
    user = User(
        email=f"{TEST_EMAIL_PREFIX}-{suffix}@example.com",
        username=f"test_ingestion_{suffix}",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    document = Document(
        user_id=user.id,
        document_name="notes.txt",
        document_type="txt",
        mime_type="text/plain",
        storage_path=f"personal/{user.id}/notes-{suffix}.txt",
        file_size=len(content_hash),
        content_hash=content_hash,
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)
    return document


@pytest.fixture(autouse=True)
def _local_storage_with_text(tmp_path, monkeypatch):
    """Ingestion reads the document back from storage -- write real text
    content under each test's storage_path so parsing has something to
    chunk. Patches get_storage_provider used by the ingestion service."""
    provider = LocalFilesystemStorageProvider(tmp_path)
    monkeypatch.setattr(
        "app.service.document_ingestion_service.get_storage_provider",
        lambda: provider,
    )
    return provider


@pytest.fixture(autouse=True)
def _fake_vector_store(monkeypatch):
    """Avoids real Pinecone calls in the automated test suite -- vector
    storage itself is exercised separately (see manual verification)."""
    fake = _FakeVectorStore()
    monkeypatch.setattr(
        "app.service.document_ingestion_service.get_vector_store",
        lambda **kwargs: fake,
    )
    return fake


@pytest.fixture(autouse=True)
def _fake_embeddings(monkeypatch):
    """Avoids real Gemini API calls in the automated test suite -- embedding
    quality itself is exercised separately (see manual verification)."""

    async def _fake_embed_texts(texts: list[str]) -> list[list[float]]:
        return [[0.0] * EmbeddingService.DIMENSIONS for _ in texts]

    monkeypatch.setattr(
        "app.service.document_ingestion_service.EmbeddingService.embed_texts",
        _fake_embed_texts,
    )


@pytest.mark.asyncio
async def test_process_marks_document_completed_and_increments_usage(tmp_path):
    async with async_session_factory() as session:
        document = await _make_document(session, "a", content_hash=f"{'a':0<64}"[:64])
        full_path = tmp_path / document.storage_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(b"This is a short test document about knowledge bases.")

        await DocumentIngestionService.process(session, document.id)

        await session.refresh(document)
        assert document.status == "completed"
        assert document.processed_at is not None
        assert document.chunk_count >= 1

        counter_result = await session.execute(
            select(UsageCounter).where(UsageCounter.user_id == document.user_id)
        )
        counter = counter_result.scalar_one()
        assert counter.documents_uploaded_count == 1


@pytest.mark.asyncio
async def test_process_increments_existing_usage_counter(tmp_path):
    async with async_session_factory() as session:
        document = await _make_document(session, "b", content_hash=f"{'b':0<64}"[:64])
        session.add(UsageCounter(user_id=document.user_id, documents_uploaded_count=3))
        await session.commit()

        full_path = tmp_path / document.storage_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(b"Another short document for chunking.")

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
        # been deleted before the task got to it.
        await DocumentIngestionService.process(session, uuid.uuid4())
