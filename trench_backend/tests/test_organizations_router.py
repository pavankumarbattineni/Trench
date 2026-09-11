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


async def _promote_to_trench_admin(email: str) -> None:
    """Directly sets role="admin" for a test user, bypassing the app-level
    "only while no admin exists yet" bootstrap gate (see
    UserService.create_user) -- the same kind of direct, ops-only
    provisioning a real deployment would use to seed its first admin(s),
    just done here so every identity in this org-focused test file can
    create its own organization regardless of signup order. This file is
    about organization-level roles, not Trench-level ones -- separate,
    dedicated tests (test_auth_flow.py) cover the Trench-admin bootstrap
    and non-admin restriction themselves."""
    async with async_session_factory() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalar_one()
        user.role = "admin"
        await session.commit()


async def _login(client: AsyncClient, uid: str, email: str) -> None:
    """Signs up (idempotently -- a 409 for an already-registered email is
    fine here), promotes to Trench admin (see `_promote_to_trench_admin`)
    so this identity can create organizations, then signs in. Sets the
    client's default Authorization header so every subsequent request on
    this client is sent as this user -- until the next `_login()` call
    overwrites it with a different user's token."""
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_claims(uid, email)
    ):
        await client.post("/api/v1/auth/signup", json={"id_token": "fake"})
        await _promote_to_trench_admin(email)
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
        f"/api/v1/organizations/{organization['id']}/knowledge-access",
        params={"user_id": member_profile["id"], "allow_access": True},
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
    member_list_body = member_list_response.json()
    assert member_list_body["total"] == 2
    assert len(member_list_body["items"]) == 2

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
        f"/api/v1/organizations/{organization['id']}/knowledge-access",
        params={"user_id": member_profile["id"], "allow_access": True},
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
    members = members_response.json()["items"]
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
        f"/api/v1/organizations/{organization['id']}/members",
        params={"user_id": owner_profile["id"]},
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
        f"/api/v1/organizations/{organization['id']}/knowledge-access",
        params={"user_id": owner_profile["id"]},
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
        f"/api/v1/organizations/{organization['id']}/knowledge-access",
        params={"user_id": member_profile["id"], "allow_access": True},
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


# --- Trench admin vs organization admin (independent axes) -----------------


async def _login_without_trench_admin(
    client: AsyncClient, uid: str, email: str
) -> None:
    """Like `_login`, but does NOT promote to Trench admin -- for tests
    specifically about the ordinary (role="user") case."""
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_claims(uid, email)
    ):
        await client.post("/api/v1/auth/signup", json={"id_token": "fake"})
        response = await client.post("/api/v1/auth/login", json={"id_token": "fake"})
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"


@pytest.mark.asyncio
async def test_non_trench_admin_cannot_create_an_organization(client: AsyncClient):
    domain = f"{PREFIX}-15.com"
    await _login_without_trench_admin(client, f"{PREFIX}-plain-15", f"plain@{domain}")

    me = (await client.get("/api/v1/users/me")).json()
    assert me["role"] == "user"

    response = await client.post(
        "/api/v1/organizations", json={"name": f"{PREFIX}-org-15"}
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_trench_admin_and_organization_admin_roles_are_independent(
    client: AsyncClient,
):
    domain = f"{PREFIX}-16.com"
    # Owner is a Trench admin (via _login's promotion) -- creates the org
    # and becomes its owner/org-admin.
    await _login(client, f"{PREFIX}-owner-16", f"owner@{domain}")
    organization = (
        await client.post("/api/v1/organizations", json={"name": f"{PREFIX}-org-16"})
    ).json()

    # A second user signs up as an ordinary Trench user (role="user"), not
    # a Trench admin.
    await _login_without_trench_admin(client, f"{PREFIX}-plain-16", f"plain@{domain}")
    plain_profile = (await client.get("/api/v1/users/me")).json()
    assert plain_profile["role"] == "user"

    # The owner (Trench admin) makes this ordinary Trench user an *org*
    # admin -- an org-level role, granted independently of their Trench
    # role.
    await _login(client, f"{PREFIX}-owner-16", f"owner@{domain}")
    await client.post(
        f"/api/v1/organizations/{organization['id']}/members",
        json={"username": plain_profile["username"], "role": "admin"},
    )

    # From the plain user's own perspective: Trench role stays "user", but
    # their organization role is now "admin" -- the two are independent
    # fields on the same profile.
    await _login_without_trench_admin(client, f"{PREFIX}-plain-16", f"plain@{domain}")
    refreshed_profile = (await client.get("/api/v1/users/me")).json()
    assert refreshed_profile["role"] == "user"
    assert refreshed_profile["organization"]["role"] == "admin"

    # Being an org admin does NOT make them a Trench admin: they still
    # can't create a (second, unrelated) organization at the Trench level
    # -- this 403 fires from require_trench_admin before OrganizationService
    # ever gets to check "you already belong to one".
    denied = await client.post(
        "/api/v1/organizations", json={"name": f"{PREFIX}-org-16-second"}
    )
    assert denied.status_code == 403

    # But being an org admin (despite role="user" at the Trench level) DOES
    # let them perform org-admin actions, e.g. adding another member.
    await _login_without_trench_admin(
        client, f"{PREFIX}-third-16", f"third@{domain}"
    )
    third_profile = (await client.get("/api/v1/users/me")).json()

    await _login_without_trench_admin(client, f"{PREFIX}-plain-16", f"plain@{domain}")
    add_response = await client.post(
        f"/api/v1/organizations/{organization['id']}/members",
        json={"username": third_profile["username"]},
    )
    assert add_response.status_code == 200


# --- Members pagination and search ------------------------------------------


@pytest.mark.asyncio
async def test_list_members_defaults_to_a_page_size_of_10(client: AsyncClient):
    domain = f"{PREFIX}-17.com"
    await _login(client, f"{PREFIX}-admin-17", f"admin@{domain}")
    organization = (
        await client.post("/api/v1/organizations", json={"name": f"{PREFIX}-org-17"})
    ).json()

    for i in range(11):
        await _login_without_trench_admin(
            client, f"{PREFIX}-m17-{i}", f"m17-{i}-{PREFIX}@{domain}"
        )
    await _login(client, f"{PREFIX}-admin-17", f"admin@{domain}")
    for i in range(11):
        await client.post(
            f"/api/v1/organizations/{organization['id']}/members",
            json={"username": f"m17-{i}-{PREFIX}"},
        )

    first_page = (
        await client.get(f"/api/v1/organizations/{organization['id']}/members")
    ).json()
    assert first_page["page"] == 1
    assert first_page["page_size"] == 10
    assert first_page["total"] == 12  # admin + 11 members
    assert len(first_page["items"]) == 10
    assert first_page["total_pages"] == 2

    second_page = (
        await client.get(
            f"/api/v1/organizations/{organization['id']}/members",
            params={"page": 2},
        )
    ).json()
    assert len(second_page["items"]) == 2


@pytest.mark.asyncio
async def test_list_members_search_matches_username_or_email(client: AsyncClient):
    domain = f"{PREFIX}-18.com"
    await _login(client, f"{PREFIX}-admin-18", f"admin@{domain}")
    organization = (
        await client.post("/api/v1/organizations", json={"name": f"{PREFIX}-org-18"})
    ).json()

    await _login_without_trench_admin(
        client, f"{PREFIX}-alice-18", f"alice-18-{PREFIX}@{domain}"
    )
    await _login_without_trench_admin(
        client, f"{PREFIX}-bob-18", f"unique-bob-mailbox-18-{PREFIX}@{domain}"
    )
    await _login(client, f"{PREFIX}-admin-18", f"admin@{domain}")
    await client.post(
        f"/api/v1/organizations/{organization['id']}/members",
        json={"username": f"alice-18-{PREFIX}"},
    )
    await client.post(
        f"/api/v1/organizations/{organization['id']}/members",
        json={"username": f"unique-bob-mailbox-18-{PREFIX}"},
    )

    by_username = (
        await client.get(
            f"/api/v1/organizations/{organization['id']}/members",
            params={"search": "alice-18"},
        )
    ).json()
    assert by_username["total"] == 1
    assert by_username["items"][0]["username"] == f"alice-18-{PREFIX}"

    by_email = (
        await client.get(
            f"/api/v1/organizations/{organization['id']}/members",
            params={"search": "unique-bob-mailbox"},
        )
    ).json()
    assert by_email["total"] == 1
    assert by_email["items"][0]["username"] == f"unique-bob-mailbox-18-{PREFIX}"

    no_match = (
        await client.get(
            f"/api/v1/organizations/{organization['id']}/members",
            params={"search": f"{PREFIX}-nobody-matches-this"},
        )
    ).json()
    assert no_match["total"] == 0
    assert no_match["items"] == []


# --- Bulk knowledge-access revocation ----------------------------------------


@pytest.mark.asyncio
async def test_remove_access_revokes_every_members_knowledge_access(
    client: AsyncClient,
):
    domain = f"{PREFIX}-19.com"
    await _login(client, f"{PREFIX}-admin-19", f"admin@{domain}")
    organization = (
        await client.post("/api/v1/organizations", json={"name": f"{PREFIX}-org-19"})
    ).json()

    member_ids = []
    for i in range(2):
        await _login_without_trench_admin(
            client, f"{PREFIX}-m19-{i}", f"m19-{i}@{domain}"
        )
        profile = (await client.get("/api/v1/users/me")).json()
        member_ids.append(profile["id"])

    await _login(client, f"{PREFIX}-admin-19", f"admin@{domain}")
    for i, member_id in enumerate(member_ids):
        await client.post(
            f"/api/v1/organizations/{organization['id']}/members",
            json={"username": f"{PREFIX}-m19-{i}"},
        )
        await client.post(
            f"/api/v1/organizations/{organization['id']}/knowledge-access",
            params={"user_id": member_id, "allow_access": True},
        )

    members_before = (
        await client.get(f"/api/v1/organizations/{organization['id']}/members")
    ).json()["items"]
    granted_before = [m for m in members_before if m["user_id"] in member_ids]
    assert all(m["has_company_access"] for m in granted_before)

    bulk_response = await client.delete(
        f"/api/v1/organizations/{organization['id']}/knowledge-access",
        params={"remove_access": True},
    )
    assert bulk_response.status_code == 200
    assert bulk_response.json()["revoked_count"] == 2

    members_after = (
        await client.get(f"/api/v1/organizations/{organization['id']}/members")
    ).json()["items"]
    for member in members_after:
        if member["user_id"] in member_ids:
            assert member["has_company_access"] is False


@pytest.mark.asyncio
async def test_revoke_knowledge_access_rejects_ambiguous_or_missing_params(
    client: AsyncClient,
):
    domain = f"{PREFIX}-20.com"
    await _login(client, f"{PREFIX}-admin-20", f"admin@{domain}")
    organization = (
        await client.post("/api/v1/organizations", json={"name": f"{PREFIX}-org-20"})
    ).json()
    admin_profile = (await client.get("/api/v1/users/me")).json()

    both_given = await client.delete(
        f"/api/v1/organizations/{organization['id']}/knowledge-access",
        params={"user_id": admin_profile["id"], "remove_access": True},
    )
    assert both_given.status_code == 422

    neither_given = await client.delete(
        f"/api/v1/organizations/{organization['id']}/knowledge-access"
    )
    assert neither_given.status_code == 422


# --- Bulk knowledge-access grant, bulk member removal, sort order -----------


@pytest.mark.asyncio
async def test_access_all_grants_every_current_member(client: AsyncClient):
    domain = f"{PREFIX}-21.com"
    await _login(client, f"{PREFIX}-admin-21", f"admin@{domain}")
    organization = (
        await client.post("/api/v1/organizations", json={"name": f"{PREFIX}-org-21"})
    ).json()

    member_ids = []
    for i in range(2):
        await _login_without_trench_admin(
            client, f"{PREFIX}-m21-{i}", f"m21-{i}-{PREFIX}@{domain}"
        )
        profile = (await client.get("/api/v1/users/me")).json()
        member_ids.append(profile["id"])

    await _login(client, f"{PREFIX}-admin-21", f"admin@{domain}")
    for i in range(2):
        await client.post(
            f"/api/v1/organizations/{organization['id']}/members",
            json={"username": f"m21-{i}-{PREFIX}"},
        )

    members_before = (
        await client.get(f"/api/v1/organizations/{organization['id']}/members")
    ).json()["items"]
    targeted_before = [m for m in members_before if m["user_id"] in member_ids]
    assert all(not m["has_company_access"] for m in targeted_before)

    grant_response = await client.post(
        f"/api/v1/organizations/{organization['id']}/knowledge-access",
        params={"access_all": True},
    )
    assert grant_response.status_code == 200
    # 2 members + the admin (who gets a harmless grant row too, even
    # though their effective access already came from their role).
    assert grant_response.json()["granted_count"] == 3

    members_after = (
        await client.get(f"/api/v1/organizations/{organization['id']}/members")
    ).json()["items"]
    targeted_after = [m for m in members_after if m["user_id"] in member_ids]
    assert all(m["has_company_access"] for m in targeted_after)


@pytest.mark.asyncio
async def test_update_knowledge_access_rejects_ambiguous_or_missing_params(
    client: AsyncClient,
):
    domain = f"{PREFIX}-22.com"
    await _login(client, f"{PREFIX}-admin-22", f"admin@{domain}")
    organization = (
        await client.post("/api/v1/organizations", json={"name": f"{PREFIX}-org-22"})
    ).json()
    admin_profile = (await client.get("/api/v1/users/me")).json()

    both_given = await client.post(
        f"/api/v1/organizations/{organization['id']}/knowledge-access",
        params={
            "user_id": admin_profile["id"],
            "allow_access": True,
            "access_all": True,
        },
    )
    assert both_given.status_code == 422

    neither_given = await client.post(
        f"/api/v1/organizations/{organization['id']}/knowledge-access"
    )
    assert neither_given.status_code == 422

    missing_allow_access = await client.post(
        f"/api/v1/organizations/{organization['id']}/knowledge-access",
        params={"user_id": admin_profile["id"]},
    )
    assert missing_allow_access.status_code == 422


@pytest.mark.asyncio
async def test_remove_all_removes_every_member_except_owner_and_caller(
    client: AsyncClient,
):
    domain = f"{PREFIX}-23.com"
    await _login(client, f"{PREFIX}-owner-23", f"owner@{domain}")
    organization = (
        await client.post("/api/v1/organizations", json={"name": f"{PREFIX}-org-23"})
    ).json()
    owner_profile = (await client.get("/api/v1/users/me")).json()

    for i in range(2):
        await _login_without_trench_admin(
            client, f"{PREFIX}-m23-{i}", f"m23-{i}-{PREFIX}@{domain}"
        )
    await _login(client, f"{PREFIX}-owner-23", f"owner@{domain}")
    for i in range(2):
        await client.post(
            f"/api/v1/organizations/{organization['id']}/members",
            json={"username": f"m23-{i}-{PREFIX}"},
        )

    before = (
        await client.get(f"/api/v1/organizations/{organization['id']}/members")
    ).json()
    assert before["total"] == 3  # owner + 2 members

    bulk_response = await client.delete(
        f"/api/v1/organizations/{organization['id']}/members",
        params={"remove_all": True},
    )
    assert bulk_response.status_code == 200
    assert bulk_response.json()["removed_count"] == 2

    after = (
        await client.get(f"/api/v1/organizations/{organization['id']}/members")
    ).json()
    assert after["total"] == 1
    assert after["items"][0]["user_id"] == owner_profile["id"]


@pytest.mark.asyncio
async def test_remove_member_rejects_ambiguous_or_missing_params(client: AsyncClient):
    domain = f"{PREFIX}-24.com"
    await _login(client, f"{PREFIX}-admin-24", f"admin@{domain}")
    organization = (
        await client.post("/api/v1/organizations", json={"name": f"{PREFIX}-org-24"})
    ).json()
    admin_profile = (await client.get("/api/v1/users/me")).json()

    both_given = await client.delete(
        f"/api/v1/organizations/{organization['id']}/members",
        params={"user_id": admin_profile["id"], "remove_all": True},
    )
    assert both_given.status_code == 422

    neither_given = await client.delete(
        f"/api/v1/organizations/{organization['id']}/members"
    )
    assert neither_given.status_code == 422


@pytest.mark.asyncio
async def test_members_are_sorted_owner_then_admins_then_members(client: AsyncClient):
    domain = f"{PREFIX}-25.com"
    await _login(client, f"{PREFIX}-owner-25", f"owner@{domain}")
    organization = (
        await client.post("/api/v1/organizations", json={"name": f"{PREFIX}-org-25"})
    ).json()
    owner_profile = (await client.get("/api/v1/users/me")).json()

    # A regular member added first (so insertion order alone would put
    # them ahead of an admin added later, if not for the explicit sort).
    await _login_without_trench_admin(
        client, f"{PREFIX}-early-member-25", f"early-member-{PREFIX}@{domain}"
    )
    early_member_profile = (await client.get("/api/v1/users/me")).json()

    await _login_without_trench_admin(
        client, f"{PREFIX}-later-admin-25", f"later-admin-{PREFIX}@{domain}"
    )
    later_admin_profile = (await client.get("/api/v1/users/me")).json()

    await _login(client, f"{PREFIX}-owner-25", f"owner@{domain}")
    await client.post(
        f"/api/v1/organizations/{organization['id']}/members",
        json={"username": f"early-member-{PREFIX}"},
    )
    await client.post(
        f"/api/v1/organizations/{organization['id']}/members",
        json={"username": f"later-admin-{PREFIX}", "role": "admin"},
    )

    members = (
        await client.get(f"/api/v1/organizations/{organization['id']}/members")
    ).json()["items"]
    ordered_user_ids = [m["user_id"] for m in members]
    assert ordered_user_ids == [
        owner_profile["id"],
        later_admin_profile["id"],
        early_member_profile["id"],
    ]
