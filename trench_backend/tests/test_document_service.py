from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.database.models import Document, UsageCounter, User
from app.database.session import async_session_factory
from app.service.credential_service import CredentialService
from app.service.document_service import DocumentService
from app.service.document_storage_service import LocalFilesystemStorageProvider

TEST_EMAIL_PREFIX = "test-documents"


async def _make_user(session, suffix: str) -> User:
    user = User(
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
def _no_real_ingestion(monkeypatch):
    """upload_document fires a real background asyncio task for ingestion
    -- these tests are about upload/list/get/delete business logic, not
    the ingestion pipeline (see test_document_ingestion_service.py), so
    replace it with a no-op to avoid real Pinecone/LlamaParse calls."""

    async def _noop(document_id) -> None:
        return None

    monkeypatch.setattr(DocumentService, "_run_ingestion", staticmethod(_noop))


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


@pytest.mark.asyncio
async def test_upload_creates_pending_document(tmp_path):
    async with async_session_factory() as session:
        user = await _make_user(session, "upload")

        with _local_storage(tmp_path):
            document = await DocumentService.upload_document(
                session,
                user=user,
                filename="notes.txt",
                content=b"hello knowledge base",
            )

        assert document.status == "pending"
        assert document.mime_type == "text/plain"
        assert document.document_type == "txt"
        assert document.knowledge_type == "personal"
        assert document.knowledge_base == "own"
        assert document.file_size == len(b"hello knowledge base")

        stored_path = tmp_path / document.storage_path
        assert stored_path.read_bytes() == b"hello knowledge base"


@pytest.mark.asyncio
async def test_duplicate_upload_short_circuits_without_new_row(tmp_path):
    async with async_session_factory() as session:
        user = await _make_user(session, "dup")

        with _local_storage(tmp_path):
            first = await DocumentService.upload_document(
                session, user=user, filename="a.txt", content=b"same content"
            )
            second = await DocumentService.upload_document(
                session, user=user, filename="b.txt", content=b"same content"
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
                session, user=user, filename="one-more.txt", content=b"over the limit"
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
            )
        assert document.status == "pending"


@pytest.mark.asyncio
async def test_upload_blocked_while_another_document_is_processing(tmp_path):
    async with async_session_factory() as session:
        user = await _make_user(session, "concurrent")

        with _local_storage(tmp_path):
            first = await DocumentService.upload_document(
                session, user=user, filename="first.txt", content=b"first content"
            )
        assert first.status == "pending"

        with (
            _local_storage(tmp_path),
            pytest.raises(HTTPException) as exc_info,
        ):
            await DocumentService.upload_document(
                session, user=user, filename="second.txt", content=b"second content"
            )
        assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_upload_allowed_again_once_prior_document_completed(tmp_path):
    async with async_session_factory() as session:
        user = await _make_user(session, "sequential")

        with _local_storage(tmp_path):
            first = await DocumentService.upload_document(
                session, user=user, filename="first.txt", content=b"first content"
            )
        first.status = "completed"
        await session.commit()

        with _local_storage(tmp_path):
            second = await DocumentService.upload_document(
                session, user=user, filename="second.txt", content=b"second content"
            )
        assert second.status == "pending"
        assert second.id != first.id


@pytest.mark.asyncio
async def test_delete_removes_row_and_file(tmp_path):
    async with async_session_factory() as session:
        user = await _make_user(session, "delete")

        with _local_storage(tmp_path):
            document = await DocumentService.upload_document(
                session, user=user, filename="to-delete.txt", content=b"temporary"
            )
            stored_path = tmp_path / document.storage_path
            assert stored_path.exists()

            # delete_document refuses to remove a pending/processing
            # document (it would race the background ingestion task) -- so
            # move it to a terminal status first, as real ingestion would.
            document.status = "completed"
            await session.commit()

            await DocumentService.delete_document(
                session, user=user, document_id=document.id
            )

        assert not stored_path.exists()
        with pytest.raises(HTTPException):
            await DocumentService.get_document(
                session, user=user, document_id=document.id
            )


@pytest.mark.asyncio
async def test_personal_document_not_visible_to_another_user(tmp_path):
    async with async_session_factory() as session:
        owner = await _make_user(session, "owner")
        other = await _make_user(session, "other")

        with _local_storage(tmp_path):
            document = await DocumentService.upload_document(
                session, user=owner, filename="private.txt", content=b"owner only"
            )

        with pytest.raises(HTTPException) as exc_info:
            await DocumentService.get_document(
                session, user=other, document_id=document.id
            )
        assert exc_info.value.status_code == 404
