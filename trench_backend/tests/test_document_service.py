from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.database.models import Document, Tenant, User
from app.database.session import async_session_factory
from app.service.credential_service import CredentialService
from app.service.document_service import DocumentService
from app.service.document_storage_service import (
    DocumentStorageError,
    LocalFilesystemStorageProvider,
)

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
        user.documents_uploaded_count = 5
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
        user.documents_uploaded_count = 5
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


class _FailingStorage(LocalFilesystemStorageProvider):
    """Local storage whose every operation fails the way B2StorageProvider
    reports a backend failure."""

    async def save(self, path: str, content: bytes) -> None:
        raise DocumentStorageError("boom")

    async def delete(self, path: str) -> None:
        raise DocumentStorageError("boom")

    async def generate_presigned_url(self, path: str, **kwargs) -> str:
        raise DocumentStorageError("boom")


class _SigningStorage(LocalFilesystemStorageProvider):
    """Local storage that also "signs" download URLs, recording each call."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.presign_calls: list[tuple[str, dict]] = []

    async def generate_presigned_url(self, path: str, **kwargs) -> str:
        self.presign_calls.append((path, kwargs))
        return f"https://signed.example/{path}"


@pytest.mark.asyncio
async def test_upload_storage_failure_is_502_and_creates_no_row(tmp_path):
    async with async_session_factory() as session:
        user = await _make_user(session, "savefail")

        with (
            patch(
                "app.service.document_service.get_storage_provider",
                return_value=_FailingStorage(tmp_path),
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            await DocumentService.upload_document(
                session, user=user, filename="notes.txt", content=b"never stored"
            )
        assert exc_info.value.status_code == 502

        result = await session.execute(
            select(Document).where(Document.user_id == user.id)
        )
        assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_delete_still_removes_row_when_storage_delete_fails(tmp_path):
    async with async_session_factory() as session:
        user = await _make_user(session, "delfail")

        with _local_storage(tmp_path):
            document = await DocumentService.upload_document(
                session, user=user, filename="orphan.txt", content=b"orphaned"
            )
        document.status = "completed"
        await session.commit()

        with patch(
            "app.service.document_service.get_storage_provider",
            return_value=_FailingStorage(tmp_path),
        ):
            await DocumentService.delete_document(
                session, user=user, document_id=document.id
            )

        result = await session.execute(
            select(Document).where(Document.id == document.id)
        )
        assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_generate_download_url_passes_document_metadata(tmp_path):
    storage = _SigningStorage(tmp_path)
    async with async_session_factory() as session:
        user = await _make_user(session, "download")

        with patch(
            "app.service.document_service.get_storage_provider",
            return_value=storage,
        ):
            document = await DocumentService.upload_document(
                session, user=user, filename="notes.txt", content=b"downloadable"
            )
            url = await DocumentService.generate_download_url(
                session, user=user, document_id=document.id, disposition="attachment"
            )

        assert url == f"https://signed.example/{document.storage_path}"
        assert storage.presign_calls == [
            (
                document.storage_path,
                {
                    "filename": "notes.txt",
                    "mime_type": "text/plain",
                    "disposition": "attachment",
                    "expires_in": 300,
                },
            )
        ]


@pytest.mark.asyncio
async def test_generate_download_url_denied_to_another_user(tmp_path):
    storage = _SigningStorage(tmp_path)
    async with async_session_factory() as session:
        owner = await _make_user(session, "dlowner")
        other = await _make_user(session, "dlother")

        with patch(
            "app.service.document_service.get_storage_provider",
            return_value=storage,
        ):
            document = await DocumentService.upload_document(
                session, user=owner, filename="private.txt", content=b"owner only"
            )
            with pytest.raises(HTTPException) as exc_info:
                await DocumentService.generate_download_url(
                    session, user=other, document_id=document.id, disposition="inline"
                )
        assert exc_info.value.status_code == 404
        assert storage.presign_calls == []


@pytest.mark.asyncio
async def test_generate_download_url_storage_failure_is_502(tmp_path):
    async with async_session_factory() as session:
        user = await _make_user(session, "dlfail")

        with _local_storage(tmp_path):
            document = await DocumentService.upload_document(
                session, user=user, filename="notes.txt", content=b"sign me"
            )

        with (
            patch(
                "app.service.document_service.get_storage_provider",
                return_value=_FailingStorage(tmp_path),
            ),
            pytest.raises(HTTPException) as exc_info,
        ):
            await DocumentService.generate_download_url(
                session, user=user, document_id=document.id, disposition="inline"
            )
        assert exc_info.value.status_code == 502


def _document(
    *, user_id, knowledge_type, status, tenant_id=None, suffix="x"
) -> Document:
    return Document(
        user_id=user_id,
        tenant_id=tenant_id,
        document_name=f"{suffix}.txt",
        document_type="txt",
        mime_type="text/plain",
        storage_path=f"{knowledge_type}/{suffix}/{suffix}.txt",
        file_size=1,
        content_hash=f"hash-{suffix}",
        status=status,
        knowledge_type=knowledge_type,
        knowledge_base="default" if knowledge_type == "company" else "own",
    )


@pytest.mark.asyncio
async def test_has_any_personal_documents_false_when_none_exist():
    async with async_session_factory() as session:
        user = await _make_user(session, "empty-personal")
        assert await DocumentService.has_any_personal_documents(
            session, user_id=user.id
        ) is False


@pytest.mark.asyncio
async def test_has_any_personal_documents_true_once_one_completes():
    async with async_session_factory() as session:
        user = await _make_user(session, "has-personal")
        session.add(
            _document(
                user_id=user.id,
                knowledge_type="personal",
                status="completed",
                suffix=str(user.id),
            )
        )
        await session.commit()
        assert await DocumentService.has_any_personal_documents(
            session, user_id=user.id
        ) is True


@pytest.mark.asyncio
async def test_has_any_personal_documents_ignores_pending_documents():
    """A document that hasn't finished ingesting yet has no searchable
    content -- it shouldn't count as "this knowledge base has something in
    it" for the empty-knowledge-base chat check (see rag_graph.py)."""
    async with async_session_factory() as session:
        user = await _make_user(session, "pending-only")
        session.add(
            _document(
                user_id=user.id,
                knowledge_type="personal",
                status="pending",
                suffix=str(user.id),
            )
        )
        await session.commit()
        assert await DocumentService.has_any_personal_documents(
            session, user_id=user.id
        ) is False


@pytest.mark.asyncio
async def test_has_any_company_documents_is_scoped_to_the_tenant():
    async with async_session_factory() as session:
        user = await _make_user(session, "company-scope")
        tenant = Tenant(
            name=f"test-documents-tenant-{user.id}",
            domain=f"test-documents-{user.id}.example.com",
        )
        other_tenant = Tenant(
            name=f"test-documents-other-tenant-{user.id}",
            domain=f"test-documents-other-{user.id}.example.com",
        )
        session.add_all([tenant, other_tenant])
        await session.commit()
        await session.refresh(tenant)
        await session.refresh(other_tenant)

        session.add(
            _document(
                user_id=user.id,
                tenant_id=other_tenant.id,
                knowledge_type="company",
                status="completed",
                suffix=str(user.id),
            )
        )
        await session.commit()

        assert await DocumentService.has_any_company_documents(
            session, tenant_id=tenant.id
        ) is False
        assert await DocumentService.has_any_company_documents(
            session, tenant_id=other_tenant.id
        ) is True

        await session.delete(tenant)
        await session.delete(other_tenant)
        await session.commit()
