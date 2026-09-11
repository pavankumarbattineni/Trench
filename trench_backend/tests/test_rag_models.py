import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database.models import Document, UsageCounter, User
from app.database.session import async_session_factory


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.email.like("test-rag-models%"))
        )
        for user in result.scalars().all():
            await session.delete(user)
        await session.commit()


async def _make_user(session, suffix: str) -> User:
    user = User(
        email=f"test-rag-models-{suffix}@example.com",
        username=f"test_rag_models_{suffix}",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.mark.asyncio
async def test_document_cascades_on_user_delete():
    async with async_session_factory() as session:
        user = await _make_user(session, "cascade")

        document = Document(
            user_id=user.id,
            document_name="notes.md",
            document_type="md",
            mime_type="text/markdown",
            storage_path="personal/x/notes.md",
            file_size=1024,
            content_hash="a" * 64,
        )
        session.add(document)
        await session.commit()
        await session.refresh(document)

        assert document.status == "pending"
        assert document.knowledge_type == "personal"
        assert document.knowledge_base == "own"
        assert document.chunk_count == 0

        counter = UsageCounter(user_id=user.id, documents_uploaded_count=1)
        session.add(counter)
        await session.commit()

        # Deleting the user cascades through documents.
        await session.delete(user)
        await session.commit()

        remaining = await session.execute(
            select(Document).where(Document.id == document.id)
        )
        assert remaining.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_document_content_hash_unique_per_user():
    async with async_session_factory() as session:
        user = await _make_user(session, "dup")

        dup_hash = "b" * 64
        session.add(
            Document(
                user_id=user.id,
                document_name="a.txt",
                document_type="txt",
                mime_type="text/plain",
                storage_path="a",
                file_size=10,
                content_hash=dup_hash,
            )
        )
        await session.commit()

        session.add(
            Document(
                user_id=user.id,
                document_name="b.txt",
                document_type="txt",
                mime_type="text/plain",
                storage_path="b",
                file_size=10,
                content_hash=dup_hash,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
