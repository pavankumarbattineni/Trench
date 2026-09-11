import time
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.database.models import Document, Organization, User
from app.database.session import async_session_factory
from app.service.document_service import DocumentService
from app.service.document_storage_service import LocalFilesystemStorageProvider

PREFIX = "test-orgs-router"


@pytest.fixture(autouse=True)
def _no_real_ingestion(monkeypatch):
    """Company-document upload fires a real background asyncio task for
    ingestion -- these tests are about the router/API authorization
    contract, not the ingestion pipeline, so replace it with a no-op to
    avoid real Pinecone/LlamaParse calls."""

    async def _noop(document_id) -> None:
        return None

    monkeypatch.setattr(DocumentService, "_run_ingestion", staticmethod(_noop))


def _claims(uid: str, email: str) -> dict:
    return {"uid": uid, "email": email, "iat": int(time.time())}


async def _login(client: AsyncClient, uid: str, email: str) -> None:
    """Logs in and sets the client's default Authorization header so every
    subsequent request on this client is sent as this user -- until the
    next `_login()` call overwrites it with a different user's token."""
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_claims(uid, email)
    ):
        response = await client.post("/api/v1/auth/login", json={"id_token": "fake"})
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        # Organizations first -- Organization.owner_user_id FK-references
        # users, so deleting a user while their organization still
        # references them as owner would violate that constraint.
        org_result = await session.execute(
            select(Organization).where(Organization.name.like(f"{PREFIX}%"))
        )
        for organization in org_result.scalars().all():
            await session.delete(organization)
        await session.commit()

        result = await session.execute(
            select(User).where(User.email.like(f"%{PREFIX}%"))
        )
        for user in result.scalars().all():
            await session.delete(user)
        await session.commit()


@pytest.mark.asyncio
async def test_create_organization_makes_creator_admin(client: AsyncClient):
    domain = f"{PREFIX}-1.com"
    await _login(client, f"{PREFIX}-admin-1", f"admin@{domain}")

    create_response = await client.post(
        "/api/v1/organizations", json={"name": f"{PREFIX}-org-1"}
    )
    assert create_response.status_code == 200
    organization = create_response.json()
    assert organization["domain"] == domain

    me_response = await client.get("/api/v1/users/me")
    profile = me_response.json()
    assert profile["organization"]["id"] == organization["id"]
    assert profile["organization"]["role"] == "admin"
    assert profile["has_company_access"] is True

    my_org_response = await client.get("/api/v1/organizations/me")
    assert my_org_response.json()["my_role"] == "admin"


@pytest.mark.asyncio
async def test_creating_a_second_organization_while_already_in_one_is_rejected(
    client: AsyncClient,
):
    domain = f"{PREFIX}-2.com"
    await _login(client, f"{PREFIX}-admin-2", f"admin@{domain}")
    await client.post("/api/v1/organizations", json={"name": f"{PREFIX}-org-2"})

    second_response = await client.post(
        "/api/v1/organizations", json={"name": f"{PREFIX}-org-2b"}
    )
    assert second_response.status_code == 409


@pytest.mark.asyncio
async def test_creating_organization_with_public_email_domain_is_rejected(
    client: AsyncClient,
):
    await _login(client, f"{PREFIX}-public", f"{PREFIX}-public@gmail.com")

    response = await client.post(
        "/api/v1/organizations", json={"name": f"{PREFIX}-org-public"}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_creating_organization_for_an_already_claimed_domain_is_rejected(
    client: AsyncClient,
):
    domain = f"{PREFIX}-dup.com"
    await _login(client, f"{PREFIX}-dup-admin-1", f"first@{domain}")
    first_response = await client.post(
        "/api/v1/organizations", json={"name": f"{PREFIX}-org-dup-1"}
    )
    assert first_response.status_code == 200

    await _login(client, f"{PREFIX}-dup-admin-2", f"second@{domain}")
    second_response = await client.post(
        "/api/v1/organizations", json={"name": f"{PREFIX}-org-dup-2"}
    )
    assert second_response.status_code == 409


@pytest.mark.asyncio
async def test_adding_member_with_mismatched_domain_is_rejected(client: AsyncClient):
    domain = f"{PREFIX}-5.com"
    await _login(client, f"{PREFIX}-admin-5", f"admin@{domain}")
    organization = (
        await client.post("/api/v1/organizations", json={"name": f"{PREFIX}-org-5"})
    ).json()

    outsider_email = f"{PREFIX}-outsider-5@other-{PREFIX}.com"
    await _login(client, f"{PREFIX}-outsider-5", outsider_email)
    outsider_profile = (await client.get("/api/v1/users/me")).json()

    await _login(client, f"{PREFIX}-admin-5", f"admin@{domain}")
    response = await client.post(
        f"/api/v1/organizations/{organization['id']}/members",
        json={"username": outsider_profile["username"], "role": "member"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_non_admin_cannot_grant_knowledge_access(client: AsyncClient):
    domain = f"{PREFIX}-3.com"
    await _login(client, f"{PREFIX}-admin-3", f"admin@{domain}")
    organization = (
        await client.post("/api/v1/organizations", json={"name": f"{PREFIX}-org-3"})
    ).json()

    await _login(client, f"{PREFIX}-member-3", f"member@{domain}")
    member_profile = (await client.get("/api/v1/users/me")).json()

    await _login(client, f"{PREFIX}-admin-3", f"admin@{domain}")
    add_response = await client.post(
        f"/api/v1/organizations/{organization['id']}/members",
        json={"username": member_profile["username"], "role": "member"},
    )
    assert add_response.status_code == 200

    await _login(client, f"{PREFIX}-member-3", f"member@{domain}")
    denied_response = await client.post(
        f"/api/v1/organizations/{organization['id']}/knowledge-access/{member_profile['id']}"
    )
    assert denied_response.status_code == 403


@pytest.mark.asyncio
async def test_any_member_can_list_members_but_outsiders_cannot(client: AsyncClient):
    domain = f"{PREFIX}-6.com"
    await _login(client, f"{PREFIX}-admin-6", f"admin@{domain}")
    organization = (
        await client.post("/api/v1/organizations", json={"name": f"{PREFIX}-org-6"})
    ).json()

    await _login(client, f"{PREFIX}-member-6", f"member@{domain}")
    member_profile = (await client.get("/api/v1/users/me")).json()

    await _login(client, f"{PREFIX}-admin-6", f"admin@{domain}")
    await client.post(
        f"/api/v1/organizations/{organization['id']}/members",
        json={"username": member_profile["username"]},
    )

    await _login(client, f"{PREFIX}-member-6", f"member@{domain}")
    member_list_response = await client.get(
        f"/api/v1/organizations/{organization['id']}/members"
    )
    assert member_list_response.status_code == 200
    assert len(member_list_response.json()) == 2

    await _login(
        client, f"{PREFIX}-outsider-6", f"{PREFIX}-outsider-6@other-{PREFIX}.com"
    )
    outsider_response = await client.get(
        f"/api/v1/organizations/{organization['id']}/members"
    )
    assert outsider_response.status_code == 403


@pytest.mark.asyncio
async def test_admin_grants_access_then_member_can_query_company_knowledge(
    client: AsyncClient,
):
    domain = f"{PREFIX}-4.com"
    await _login(client, f"{PREFIX}-admin-4", f"admin@{domain}")
    organization = (
        await client.post("/api/v1/organizations", json={"name": f"{PREFIX}-org-4"})
    ).json()

    await _login(client, f"{PREFIX}-member-4", f"member@{domain}")
    member_profile = (await client.get("/api/v1/users/me")).json()
    assert member_profile["has_company_access"] is False

    await _login(client, f"{PREFIX}-admin-4", f"admin@{domain}")
    await client.post(
        f"/api/v1/organizations/{organization['id']}/members",
        json={"username": member_profile["username"]},
    )
    grant_response = await client.post(
        f"/api/v1/organizations/{organization['id']}/knowledge-access/{member_profile['id']}"
    )
    assert grant_response.status_code == 200

    await _login(client, f"{PREFIX}-member-4", f"member@{domain}")
    refreshed_profile = (await client.get("/api/v1/users/me")).json()
    assert refreshed_profile["has_company_access"] is True


@pytest.mark.asyncio
async def test_creating_organization_auto_adds_existing_same_domain_users(
    client: AsyncClient,
):
    domain = f"{PREFIX}-7.com"
    # Two existing users on the domain, signed up before the org exists.
    await _login(client, f"{PREFIX}-early-7a", f"early-a@{domain}")
    await _login(client, f"{PREFIX}-early-7b", f"early-b@{domain}")

    await _login(client, f"{PREFIX}-admin-7", f"admin@{domain}")
    create_response = await client.post(
        "/api/v1/organizations", json={"name": f"{PREFIX}-org-7"}
    )
    assert create_response.status_code == 200
    body = create_response.json()
    assert body["auto_added_members"] == 2

    members_response = await client.get(
        f"/api/v1/organizations/{body['id']}/members"
    )
    members = members_response.json()
    assert len(members) == 3
    auto_added = [m for m in members if m["email"] != f"admin@{domain}"]
    assert len(auto_added) == 2
    for member in auto_added:
        assert member["role"] == "member"
        assert member["has_company_access"] is False


@pytest.mark.asyncio
async def test_owner_role_cannot_be_changed_by_another_admin(client: AsyncClient):
    domain = f"{PREFIX}-8.com"
    await _login(client, f"{PREFIX}-owner-8", f"owner@{domain}")
    organization = (
        await client.post("/api/v1/organizations", json={"name": f"{PREFIX}-org-8"})
    ).json()
    owner_profile = (await client.get("/api/v1/users/me")).json()

    await _login(client, f"{PREFIX}-other-admin-8", f"other-admin@{domain}")
    other_admin_profile = (await client.get("/api/v1/users/me")).json()

    await _login(client, f"{PREFIX}-owner-8", f"owner@{domain}")
    await client.post(
        f"/api/v1/organizations/{organization['id']}/members",
        json={"username": other_admin_profile["username"], "role": "admin"},
    )

    # The other admin tries to demote the owner -- must be rejected even
    # though they themselves are an admin.
    await _login(client, f"{PREFIX}-other-admin-8", f"other-admin@{domain}")
    demote_response = await client.patch(
        f"/api/v1/organizations/{organization['id']}/members/{owner_profile['id']}",
        json={"role": "member"},
    )
    assert demote_response.status_code == 403

    remove_response = await client.delete(
        f"/api/v1/organizations/{organization['id']}/members/{owner_profile['id']}"
    )
    assert remove_response.status_code == 403


@pytest.mark.asyncio
async def test_owner_knowledge_access_cannot_be_revoked(client: AsyncClient):
    domain = f"{PREFIX}-9.com"
    await _login(client, f"{PREFIX}-owner-9", f"owner@{domain}")
    organization = (
        await client.post("/api/v1/organizations", json={"name": f"{PREFIX}-org-9"})
    ).json()
    owner_profile = (await client.get("/api/v1/users/me")).json()

    revoke_response = await client.delete(
        f"/api/v1/organizations/{organization['id']}/knowledge-access/{owner_profile['id']}"
    )
    assert revoke_response.status_code == 403


@pytest.mark.asyncio
async def test_regular_member_cannot_upload_list_or_delete_company_documents(
    client: AsyncClient,
):
    domain = f"{PREFIX}-10.com"
    await _login(client, f"{PREFIX}-admin-10", f"admin@{domain}")
    organization = (
        await client.post("/api/v1/organizations", json={"name": f"{PREFIX}-org-10"})
    ).json()

    await _login(client, f"{PREFIX}-member-10", f"member@{domain}")
    member_profile = (await client.get("/api/v1/users/me")).json()

    await _login(client, f"{PREFIX}-admin-10", f"admin@{domain}")
    await client.post(
        f"/api/v1/organizations/{organization['id']}/members",
        json={"username": member_profile["username"]},
    )

    await _login(client, f"{PREFIX}-member-10", f"member@{domain}")
    upload_response = await client.post(
        f"/api/v1/organizations/{organization['id']}/documents",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert upload_response.status_code == 403

    list_response = await client.get(
        f"/api/v1/organizations/{organization['id']}/documents"
    )
    assert list_response.status_code == 403

    delete_response = await client.delete(
        f"/api/v1/organizations/{organization['id']}/documents/{member_profile['id']}"
    )
    assert delete_response.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_upload_list_and_delete_company_documents(
    client: AsyncClient, tmp_path
):
    domain = f"{PREFIX}-11.com"
    await _login(client, f"{PREFIX}-admin-11", f"admin@{domain}")
    organization = (
        await client.post("/api/v1/organizations", json={"name": f"{PREFIX}-org-11"})
    ).json()

    with patch(
        "app.service.document_service.get_storage_provider",
        return_value=LocalFilesystemStorageProvider(tmp_path),
    ):
        upload_response = await client.post(
            f"/api/v1/organizations/{organization['id']}/documents",
            files={"file": ("notes.txt", b"hello from the admin", "text/plain")},
        )
        assert upload_response.status_code == 200
        document_id = upload_response.json()["id"]

        list_response = await client.get(
            f"/api/v1/organizations/{organization['id']}/documents"
        )
        assert list_response.status_code == 200
        assert len(list_response.json()["documents"]) == 1

        # Ingestion is stubbed out (see `_no_real_ingestion` above), so
        # move the document to a terminal status first, as the real
        # pipeline eventually would -- delete refuses a pending document.
        async with async_session_factory() as session:
            db_document = await session.get(Document, document_id)
            db_document.status = "completed"
            await session.commit()

        delete_response = await client.delete(
            f"/api/v1/organizations/{organization['id']}/documents/{document_id}"
        )
        assert delete_response.status_code == 204


@pytest.mark.asyncio
async def test_knowledge_access_lets_a_member_list_but_not_upload_or_delete(
    client: AsyncClient,
):
    """Document management has no delegation path: a member granted
    company-knowledge (query) access can view/list company documents, but
    can never upload or delete them -- that stays admin-only."""
    domain = f"{PREFIX}-12.com"
    await _login(client, f"{PREFIX}-admin-12", f"admin@{domain}")
    organization = (
        await client.post("/api/v1/organizations", json={"name": f"{PREFIX}-org-12"})
    ).json()

    await _login(client, f"{PREFIX}-member-12", f"member@{domain}")
    member_profile = (await client.get("/api/v1/users/me")).json()

    await _login(client, f"{PREFIX}-admin-12", f"admin@{domain}")
    await client.post(
        f"/api/v1/organizations/{organization['id']}/members",
        json={"username": member_profile["username"]},
    )
    await client.post(
        f"/api/v1/organizations/{organization['id']}/knowledge-access/{member_profile['id']}"
    )

    await _login(client, f"{PREFIX}-member-12", f"member@{domain}")
    list_response = await client.get(
        f"/api/v1/organizations/{organization['id']}/documents"
    )
    assert list_response.status_code == 200

    upload_response = await client.post(
        f"/api/v1/organizations/{organization['id']}/documents",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert upload_response.status_code == 403

    delete_response = await client.delete(
        f"/api/v1/organizations/{organization['id']}/documents/{member_profile['id']}"
    )
    assert delete_response.status_code == 403
