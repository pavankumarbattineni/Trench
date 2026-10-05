import time
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.database.models import Document, Tenant, User
from app.database.session import async_session_factory
from app.service.document_service import DocumentService
from app.service.document_storage_service import (
    DocumentStorageError,
    LocalFilesystemStorageProvider,
)

TEST_FIREBASE_UID = "test-documents-router-uid"
TEST_EMAIL = "owner@test-documents-router.example.com"
OTHER_FIREBASE_UID = "test-documents-router-other-uid"
OTHER_EMAIL = "owner@test-documents-router-other.example.com"


def _fake_claims(uid: str = TEST_FIREBASE_UID, email: str = TEST_EMAIL) -> dict:
    return {"uid": uid, "email": email, "iat": int(time.time())}


async def _login(
    client: AsyncClient, uid: str = TEST_FIREBASE_UID, email: str = TEST_EMAIL
) -> None:
    """Signs up as an Owner (idempotently -- a 409 for an already-registered
    email is fine here) then signs in. Each distinct identity gets its own
    email domain (not just a different local part) since a domain anchors
    exactly one tenant under the invitation-only model."""
    with patch(
        "app.utils.firebase.verify_firebase_id_token",
        return_value=_fake_claims(uid=uid, email=email),
    ):
        await client.post(
            "/api/v1/auth/signup/owner",
            json={"id_token": "fake", "tenant_name": f"org-{uid}"},
        )
        response = await client.post("/api/v1/auth/login", json={"id_token": "fake"})
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"


@pytest.fixture(autouse=True)
def _no_real_ingestion(monkeypatch):
    """upload fires a real background asyncio task for ingestion -- these
    tests are about the router/API contract, not the ingestion pipeline
    (see test_document_ingestion_service.py), so replace it with a no-op
    to avoid real Pinecone/LlamaParse calls."""

    async def _noop(document_id) -> None:
        return None

    monkeypatch.setattr(DocumentService, "_run_ingestion", staticmethod(_noop))


class _FakeVectorStore:
    async def delete_by_prefix(self, *, namespace, prefix) -> None:
        pass

    async def delete_namespace(self, *, namespace) -> None:
        pass


@pytest.fixture(autouse=True)
def _fake_vector_store(monkeypatch):
    """Document deletion now always calls delete_by_prefix (see
    DocumentService._delete_document_row) -- avoid a real Pinecone call in
    the automated test suite."""
    monkeypatch.setattr(
        "app.service.document_service.get_vector_store",
        lambda **kwargs: _FakeVectorStore(),
    )


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        await session.execute(
            update(User)
            .where(User.email.like("%test-documents-router%"))
            .values(tenant_id=None)
        )
        await session.commit()
        tenant_result = await session.execute(
            select(Tenant).where(Tenant.domain.like("%test-documents-router%"))
        )
        for tenant in tenant_result.scalars().all():
            await session.delete(tenant)
        await session.commit()
        result = await session.execute(
            select(User).where(User.email.like("%test-documents-router%"))
        )
        for user in result.scalars().all():
            await session.delete(user)
        await session.commit()


@pytest.mark.asyncio
async def test_documents_endpoints_require_authentication(client: AsyncClient):
    response = await client.get("/api/v1/documents")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_upload_list_get_and_delete_document_via_api(
    client: AsyncClient, tmp_path
):
    await _login(client)

    with patch(
        "app.service.document_service.get_storage_provider",
        return_value=LocalFilesystemStorageProvider(tmp_path),
    ):
        upload_response = await client.post(
            "/api/v1/documents",
            files={"file": ("notes.txt", b"hello from the router test", "text/plain")},
        )
        assert upload_response.status_code == 200
        document = upload_response.json()
        assert document["status"] == "pending"
        assert document["document_type"] == "txt"

        list_response = await client.get("/api/v1/documents")
        assert list_response.status_code == 200
        assert len(list_response.json()["documents"]) == 1

        get_response = await client.get(f"/api/v1/documents/{document['id']}")
        assert get_response.status_code == 200

        # Ingestion is stubbed out (see `_no_real_ingestion` above), so the
        # document would otherwise sit in "pending" forever -- delete
        # correctly refuses to remove a pending/processing document (see
        # DocumentService.delete_document), so move it to a terminal status
        # first, as the real ingestion pipeline eventually would.
        async with async_session_factory() as session:
            db_document = await session.get(Document, document["id"])
            db_document.status = "completed"
            await session.commit()

        delete_response = await client.delete(f"/api/v1/documents/{document['id']}")
        assert delete_response.status_code == 204

        list_after_delete = await client.get("/api/v1/documents")
        assert list_after_delete.json()["documents"] == []


@pytest.mark.asyncio
async def test_cannot_access_another_users_document(client: AsyncClient, tmp_path):
    await _login(client)

    with patch(
        "app.service.document_service.get_storage_provider",
        return_value=LocalFilesystemStorageProvider(tmp_path),
    ):
        upload_response = await client.post(
            "/api/v1/documents",
            files={"file": ("private.txt", b"owner-only content", "text/plain")},
        )
    document_id = upload_response.json()["id"]

    await _login(client, uid=OTHER_FIREBASE_UID, email=OTHER_EMAIL)

    response = await client.get(f"/api/v1/documents/{document_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_rejects_invalid_file(client: AsyncClient):
    await _login(client)

    response = await client.post(
        "/api/v1/documents",
        files={"file": ("archive.zip", b"not a real zip either", "application/zip")},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_rejects_upload_with_neither_file_nor_retry(client: AsyncClient):
    await _login(client)

    response = await client.post("/api/v1/documents")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_rejects_retry_true_without_document_id(client: AsyncClient):
    await _login(client)

    response = await client.post("/api/v1/documents", data={"retry": "true"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_retry_reprocesses_a_failed_document_without_a_new_file(
    client: AsyncClient, tmp_path
):
    await _login(client)

    document_id = await _upload(client, LocalFilesystemStorageProvider(tmp_path))
    async with async_session_factory() as session:
        db_document = await session.get(Document, document_id)
        db_document.status = "failed"
        db_document.error_message = "embedding is down"
        await session.commit()

    retry_response = await client.post(
        "/api/v1/documents", data={"document_id": document_id, "retry": "true"}
    )
    assert retry_response.status_code == 200
    body = retry_response.json()
    assert body["id"] == document_id
    assert body["status"] == "pending"
    assert body["error_message"] is None


@pytest.mark.asyncio
async def test_retry_rejects_a_document_that_is_not_failed_via_api(
    client: AsyncClient, tmp_path
):
    await _login(client)
    document_id = await _upload(client, LocalFilesystemStorageProvider(tmp_path))
    async with async_session_factory() as session:
        db_document = await session.get(Document, document_id)
        db_document.status = "completed"
        await session.commit()

    response = await client.post(
        "/api/v1/documents", data={"document_id": document_id, "retry": "true"}
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_retry_rejects_another_users_document(client: AsyncClient, tmp_path):
    await _login(client)
    document_id = await _upload(client, LocalFilesystemStorageProvider(tmp_path))
    async with async_session_factory() as session:
        db_document = await session.get(Document, document_id)
        db_document.status = "failed"
        await session.commit()

    await _login(client, uid=OTHER_FIREBASE_UID, email=OTHER_EMAIL)
    response = await client.post(
        "/api/v1/documents", data={"document_id": document_id, "retry": "true"}
    )
    assert response.status_code == 404


class _SigningStorage(LocalFilesystemStorageProvider):
    """Local storage that also "signs" download URLs, recording each call."""

    def __init__(self, root) -> None:
        super().__init__(root)
        self.presign_calls: list[tuple[str, dict]] = []

    async def generate_presigned_url(self, path: str, **kwargs) -> str:
        self.presign_calls.append((path, kwargs))
        return "https://signed.example/download"


class _FailingStorage(LocalFilesystemStorageProvider):
    async def save(self, path: str, content: bytes) -> None:
        raise DocumentStorageError("boom")

    async def delete(self, path: str) -> None:
        raise DocumentStorageError("boom")

    async def generate_presigned_url(self, path: str, **kwargs) -> str:
        raise DocumentStorageError("boom")


async def _upload(client: AsyncClient, storage, name: str = "notes.txt") -> str:
    with patch(
        "app.service.document_service.get_storage_provider", return_value=storage
    ):
        response = await client.post(
            "/api/v1/documents",
            files={"file": (name, b"downloadable content", "text/plain")},
        )
    assert response.status_code == 200, response.text
    return response.json()["id"]


@pytest.mark.asyncio
async def test_download_returns_presigned_url(client: AsyncClient, tmp_path):
    await _login(client)
    storage = _SigningStorage(tmp_path)
    document_id = await _upload(client, storage)

    with patch(
        "app.service.document_service.get_storage_provider", return_value=storage
    ):
        default_response = await client.get(f"/api/v1/documents/{document_id}/download")
        attachment_response = await client.get(
            f"/api/v1/documents/{document_id}/download",
            params={"disposition": "attachment"},
        )

    assert default_response.status_code == 200
    assert default_response.json() == {
        "url": "https://signed.example/download",
        "expires_in": 300,
    }
    assert attachment_response.status_code == 200
    assert [call["disposition"] for _, call in storage.presign_calls] == [
        "inline",
        "attachment",
    ]
    assert storage.presign_calls[0][1]["filename"] == "notes.txt"
    assert storage.presign_calls[0][1]["mime_type"] == "text/plain"


@pytest.mark.asyncio
async def test_download_rejects_unknown_disposition(client: AsyncClient, tmp_path):
    await _login(client)
    storage = _SigningStorage(tmp_path)
    document_id = await _upload(client, storage)

    response = await client.get(
        f"/api/v1/documents/{document_id}/download",
        params={"disposition": "evil"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_cannot_download_another_users_document(client: AsyncClient, tmp_path):
    await _login(client)
    storage = _SigningStorage(tmp_path)
    document_id = await _upload(client, storage, name="private.txt")

    await _login(client, uid=OTHER_FIREBASE_UID, email=OTHER_EMAIL)

    with patch(
        "app.service.document_service.get_storage_provider", return_value=storage
    ):
        response = await client.get(f"/api/v1/documents/{document_id}/download")
    assert response.status_code == 404
    assert storage.presign_calls == []


@pytest.mark.asyncio
async def test_download_storage_failure_is_502(client: AsyncClient, tmp_path):
    await _login(client)
    document_id = await _upload(client, LocalFilesystemStorageProvider(tmp_path))

    with patch(
        "app.service.document_service.get_storage_provider",
        return_value=_FailingStorage(tmp_path),
    ):
        response = await client.get(f"/api/v1/documents/{document_id}/download")
    assert response.status_code == 502
    assert response.json()["message"] == (
        "Failed to generate a download link. Please try again."
    )


@pytest.mark.asyncio
async def test_upload_storage_failure_is_502_without_creating_a_document(
    client: AsyncClient, tmp_path
):
    await _login(client)

    with patch(
        "app.service.document_service.get_storage_provider",
        return_value=_FailingStorage(tmp_path),
    ):
        response = await client.post(
            "/api/v1/documents",
            files={"file": ("notes.txt", b"never stored", "text/plain")},
        )
    assert response.status_code == 502
    assert response.json()["message"] == (
        "Failed to store the uploaded document. Please try again."
    )

    list_response = await client.get("/api/v1/documents")
    assert list_response.json()["documents"] == []


@pytest.mark.asyncio
async def test_delete_succeeds_even_if_storage_delete_fails(
    client: AsyncClient, tmp_path
):
    await _login(client)
    document_id = await _upload(client, LocalFilesystemStorageProvider(tmp_path))

    async with async_session_factory() as session:
        db_document = await session.get(Document, document_id)
        db_document.status = "completed"
        await session.commit()

    with patch(
        "app.service.document_service.get_storage_provider",
        return_value=_FailingStorage(tmp_path),
    ):
        response = await client.delete(f"/api/v1/documents/{document_id}")
    assert response.status_code == 204

    get_response = await client.get(f"/api/v1/documents/{document_id}")
    assert get_response.status_code == 404
