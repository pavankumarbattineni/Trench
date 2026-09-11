import time
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.database.models import Document, User
from app.database.session import async_session_factory
from app.service.document_service import DocumentService
from app.service.document_storage_service import LocalFilesystemStorageProvider

TEST_FIREBASE_UID = "test-documents-router-uid"
TEST_EMAIL = "test-documents-router@example.com"
OTHER_FIREBASE_UID = "test-documents-router-other-uid"
OTHER_EMAIL = "test-documents-router-other@example.com"


def _fake_claims(uid: str = TEST_FIREBASE_UID, email: str = TEST_EMAIL) -> dict:
    return {"uid": uid, "email": email, "iat": int(time.time())}


async def _login(
    client: AsyncClient, uid: str = TEST_FIREBASE_UID, email: str = TEST_EMAIL
) -> None:
    """Signs up (idempotently -- a 409 for an already-registered email is
    fine here) then signs in, since login no longer lazily creates a
    user."""
    with patch(
        "app.utils.firebase.verify_firebase_id_token",
        return_value=_fake_claims(uid=uid, email=email),
    ):
        await client.post("/api/v1/auth/signup", json={"id_token": "fake"})
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


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.email.like("test-documents-router%"))
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
