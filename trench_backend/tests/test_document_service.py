from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.database.models import Document, KnowledgeBase, UsageCounter, User
from app.database.session import async_session_factory
from app.service.credential_service import CredentialService
from app.service.document_service import DocumentService
from app.service.document_storage_service import LocalFilesystemStorageProvider

TEST_FIREBASE_UID_PREFIX = "test-documents"


async def _make_user(session, suffix: str) -> User:
    user = User(
        firebase_uid=f"{TEST_FIREBASE_UID_PREFIX}-{suffix}",
        email=f"test-documents-{suffix}@example.com",
        username=f"test_documents_{suffix}",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


def _local_storage(tmp_path: Path):
    return patch(
        "app.service.document_service.get_storage_provider",
        return_value=LocalFilesystemStorageProvider(tmp_path),
    )


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


@pytest.mark.asyncio
async def test_upload_creates_pending_document_and_default_knowledge_base(tmp_path):
    async with async_session_factory() as session:
        user = await _make_user(session, "upload")

        with _local_storage(tmp_path):
            document = await DocumentService.upload_document(
                session,
                user=user,
                filename="notes.txt",
                content=b"hello knowledge base",
                chunking_strategy="recursive",
                chunk_size=512,
            )

        assert document.status == "pending"
        assert document.mime_type == "text/plain"

        kb_result = await session.execute(
            select(KnowledgeBase).where(KnowledgeBase.user_id == user.id)
        )
        kb = kb_result.scalar_one()
        assert kb.embedding_model == "BAAI/bge-small-en-v1.5"

        stored_path = tmp_path / document.storage_path
        assert stored_path.read_bytes() == b"hello knowledge base"


@pytest.mark.asyncio
async def test_duplicate_upload_short_circuits_without_new_row(tmp_path):
    async with async_session_factory() as session:
        user = await _make_user(session, "dup")

        with _local_storage(tmp_path):
            first = await DocumentService.upload_document(
                session,
                user=user,
                filename="a.txt",
                content=b"same content",
                chunking_strategy="recursive",
                chunk_size=512,
            )
            second = await DocumentService.upload_document(
                session,
                user=user,
                filename="b.txt",
                content=b"same content",
                chunking_strategy="recursive",
                chunk_size=512,
            )

        assert first.id == second.id

        count_result = await session.execute(
            select(Document).where(Document.user_id == user.id)
        )
        assert len(count_result.scalars().all()) == 1


@pytest.mark.asyncio
async def test_upload_blocked_at_free_tier_limit_without_pinecone_credential():
    async with async_session_factory() as session:
        user = await _make_user(session, "limit")
        session.add(UsageCounter(user_id=user.id, documents_uploaded_count=5))
        await session.commit()

        with pytest.raises(HTTPException) as exc_info:
            await DocumentService.upload_document(
                session,
                user=user,
                filename="one-more.txt",
                content=b"over the limit",
                chunking_strategy="recursive",
                chunk_size=512,
            )
        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_upload_allowed_past_limit_with_pinecone_credential(tmp_path):
    async with async_session_factory() as session:
        user = await _make_user(session, "byok")
        session.add(UsageCounter(user_id=user.id, documents_uploaded_count=5))
        await session.commit()

        with patch(
            "app.service.credential_validation_service.CredentialValidationService.validate",
            return_value=None,
        ):
            await CredentialService.save_credential(
                session,
                user_id=user.id,
                provider_type="pinecone",
                api_key="pcsk-fake-key",
            )

        with _local_storage(tmp_path):
            document = await DocumentService.upload_document(
                session,
                user=user,
                filename="past-limit.txt",
                content=b"allowed via byok",
                chunking_strategy="recursive",
                chunk_size=512,
            )
        assert document.status == "pending"


@pytest.mark.asyncio
async def test_delete_removes_row_and_file(tmp_path):
    async with async_session_factory() as session:
        user = await _make_user(session, "delete")

        with _local_storage(tmp_path):
            document = await DocumentService.upload_document(
                session,
                user=user,
                filename="to-delete.txt",
                content=b"temporary",
                chunking_strategy="recursive",
                chunk_size=512,
            )
            stored_path = tmp_path / document.storage_path
            assert stored_path.exists()

            await DocumentService.delete_document(
                session, user_id=user.id, document_id=document.id
            )

        assert not stored_path.exists()
        with pytest.raises(HTTPException):
            await DocumentService.get_document(
                session, user_id=user.id, document_id=document.id
            )
