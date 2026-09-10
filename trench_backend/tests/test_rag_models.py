import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database.models import (
    Document,
    DocumentChunk,
    KnowledgeBase,
    UsageCounter,
    User,
)
from app.database.session import async_session_factory


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.firebase_uid.like("test-rag-models%"))
        )
        for user in result.scalars().all():
            await session.delete(user)
        await session.commit()


@pytest.mark.asyncio
async def test_knowledge_base_document_and_chunk_cascade():
    async with async_session_factory() as session:
        user = User(
            firebase_uid="test-rag-models-uid",
            email="test-rag-models@example.com",
            username="test_rag_models",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        kb = KnowledgeBase(user_id=user.id)
        session.add(kb)
        await session.commit()
        await session.refresh(kb)

        assert kb.embedding_dimensions == 384
        assert kb.vector_store_provider == "pinecone"

        document = Document(
            user_id=user.id,
            knowledge_base_id=kb.id,
            filename="notes.md",
            storage_path="users/x/notes.md",
            mime_type="text/markdown",
            file_size_bytes=1024,
            content_hash="a" * 64,
        )
        session.add(document)
        await session.commit()
        await session.refresh(document)

        assert document.status == "pending"

        chunk = DocumentChunk(
            document_id=document.id,
            knowledge_base_id=kb.id,
            chunk_index=0,
            content="hello world",
            token_count=2,
        )
        session.add(chunk)
        await session.commit()

        counter = UsageCounter(user_id=user.id, documents_uploaded_count=1)
        session.add(counter)
        await session.commit()

        # Deleting the user cascades through knowledge_base -> document -> chunk.
        await session.delete(user)
        await session.commit()

        remaining_chunks = await session.execute(select(DocumentChunk))
        assert remaining_chunks.scalars().all() == []


@pytest.mark.asyncio
async def test_document_content_hash_unique_per_user():
    async with async_session_factory() as session:
        user = User(
            firebase_uid="test-rag-models-uid-2",
            email="test-rag-models-2@example.com",
            username="test_rag_models_2",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

        kb = KnowledgeBase(user_id=user.id)
        session.add(kb)
        await session.commit()
        await session.refresh(kb)

        dup_hash = "b" * 64
        session.add(
            Document(
                user_id=user.id,
                knowledge_base_id=kb.id,
                filename="a.txt",
                storage_path="a",
                mime_type="text/plain",
                file_size_bytes=10,
                content_hash=dup_hash,
            )
        )
        await session.commit()

        session.add(
            Document(
                user_id=user.id,
                knowledge_base_id=kb.id,
                filename="b.txt",
                storage_path="b",
                mime_type="text/plain",
                file_size_bytes=10,
                content_hash=dup_hash,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
