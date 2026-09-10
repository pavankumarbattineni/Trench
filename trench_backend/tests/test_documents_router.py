import time
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.database.models import User
from app.database.session import async_session_factory
from app.service.document_storage_service import LocalFilesystemStorageProvider

TEST_FIREBASE_UID = "test-documents-router-uid"
TEST_EMAIL = "test-documents-router@example.com"
OTHER_FIREBASE_UID = "test-documents-router-other-uid"
OTHER_EMAIL = "test-documents-router-other@example.com"


def _fake_claims(uid: str = TEST_FIREBASE_UID, email: str = TEST_EMAIL) -> dict:
    return {"uid": uid, "email": email, "iat": int(time.time())}


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.firebase_uid.like("test-documents-router%"))
        )
        for user in result.scalars().all():
            await session.delete(user)
        await session.commit()


@pytest.mark.asyncio
async def test_documents_endpoints_require_authentication(client: AsyncClient):
    response = await client.get("/documents")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_upload_list_get_and_delete_document_via_api(
    client: AsyncClient, tmp_path
):
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        await client.post("/auth/session", json={"id_token": "fake"})

    with patch(
        "app.service.document_service.get_storage_provider",
        return_value=LocalFilesystemStorageProvider(tmp_path),
    ):
        upload_response = await client.post(
            "/documents",
            files={"file": ("notes.txt", b"hello from the router test", "text/plain")},
        )
        assert upload_response.status_code == 200
        document = upload_response.json()
        assert document["status"] == "pending"
        assert document["mime_type"] == "text/plain"

        list_response = await client.get("/documents")
        assert list_response.status_code == 200
        assert len(list_response.json()["documents"]) == 1

        get_response = await client.get(f"/documents/{document['id']}")
        assert get_response.status_code == 200

        delete_response = await client.delete(f"/documents/{document['id']}")
        assert delete_response.status_code == 204

        list_after_delete = await client.get("/documents")
        assert list_after_delete.json()["documents"] == []


@pytest.mark.asyncio
async def test_cannot_access_another_users_document(client: AsyncClient, tmp_path):
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        await client.post("/auth/session", json={"id_token": "fake"})

    with patch(
        "app.service.document_service.get_storage_provider",
        return_value=LocalFilesystemStorageProvider(tmp_path),
    ):
        upload_response = await client.post(
            "/documents",
            files={"file": ("private.txt", b"owner-only content", "text/plain")},
        )
    document_id = upload_response.json()["id"]

    await client.post("/auth/logout")
    with patch(
        "app.utils.firebase.verify_firebase_id_token",
        return_value=_fake_claims(uid=OTHER_FIREBASE_UID, email=OTHER_EMAIL),
    ):
        await client.post("/auth/session", json={"id_token": "fake"})

    response = await client.get(f"/documents/{document_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_rejects_invalid_file(client: AsyncClient):
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims()
    ):
        await client.post("/auth/session", json={"id_token": "fake"})

    response = await client.post(
        "/documents",
        files={"file": ("archive.zip", b"not a real zip either", "application/zip")},
    )
    assert response.status_code == 422
