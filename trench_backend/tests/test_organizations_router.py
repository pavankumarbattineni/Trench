import time
import uuid
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

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


def _auth_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        # users.organization_id FK-references organizations, and
        # organizations.owner_user_id FK-references users -- a circular
        # reference, so a user's organization_id must be cleared before
        # the organization itself can be deleted. Invitation/
        # OrganizationCredential/KnowledgeAccess rows cascade-delete with
        # their organization, so no separate cleanup is needed for those.
        await session.execute(
            update(User)
            .where(User.email.like(f"%{PREFIX}%"))
            .values(organization_id=None)
        )
        await session.commit()

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


async def _signup_owner_and_create_org(client: AsyncClient) -> tuple[str, str]:
    """Signs up a new Owner (and their organization) via the invitation-only
    flow's entry point, POST /auth/signup/owner -- which no longer issues a
    session itself, so this logs in separately -- returns
    (owner_access_token, organization_id)."""
    email = f"{PREFIX}-owner-{uuid.uuid4().hex[:8]}@{PREFIX}.example.com"
    with patch(
        "app.utils.firebase.verify_firebase_id_token",
        return_value=_claims(email, email),
    ):
        response = await client.post(
            "/api/v1/auth/signup/owner",
            json={
                "id_token": "fake",
                "username": f"{PREFIX}-owner-{uuid.uuid4().hex[:6]}",
                "organization_name": f"{PREFIX}-org-{uuid.uuid4().hex[:8]}",
            },
        )
        assert response.status_code == 200, response.text
        login_response = await client.post(
            "/api/v1/auth/login", json={"id_token": "fake"}
        )
    assert login_response.status_code == 200, login_response.text
    return login_response.json()["access_token"], response.json()["organization"]["id"]


async def _invite_and_accept(
    client: AsyncClient,
    organization_id: str,
    inviter_token: str,
    *,
    role: str = "member",
    return_user_id: bool = False,
):
    """Invites a fresh email to the organization (as whoever `inviter_token`
    belongs to) and accepts it, returning the new member's access token
    (and, if requested, their user id). Reads the raw token out of the
    mocked send_email call's accept_url, since the raw token is never
    returned over HTTP (by design)."""
    email = f"{PREFIX}-invitee-{uuid.uuid4().hex[:8]}@{PREFIX}.example.com"
    with patch(
        "app.service.invitation_service.send_email", new=AsyncMock()
    ) as mock_send:
        invite_response = await client.post(
            f"/api/v1/organizations/{organization_id}/invitations",
            data={"email": email, "role": role},
            headers=_auth_headers(inviter_token),
        )
    assert invite_response.status_code == 200, invite_response.text

    html_body = mock_send.call_args.kwargs["html_body"]
    accept_url = html_body.split('href="')[1].split('"')[0]
    raw_token = parse_qs(urlparse(accept_url).query)["token"][0]

    with patch(
        "app.utils.firebase.verify_firebase_id_token",
        return_value=_claims(email, email),
    ):
        accept_response = await client.post(
            "/api/v1/auth/invitations/accept",
            json={
                "token": raw_token,
                "id_token": "fake",
                "username": f"{PREFIX}-invitee-{uuid.uuid4().hex[:6]}",
            },
        )
    assert accept_response.status_code == 200, accept_response.text
    access_token = accept_response.json()["access_token"]

    if return_user_id:
        me = await client.get("/api/v1/users/me", headers=_auth_headers(access_token))
        return access_token, me.json()["id"]
    return access_token


# --- Role matrix (Task 3) ----------------------------------------------------
#
# These depend on POST /auth/signup/owner (Task 8) and the invitation
# endpoints (Task 5) + accept endpoint (Task 6) -- skipped until Task 8
# lands, then un-skipped.


@pytest.mark.asyncio
async def test_admin_cannot_promote_a_member_to_admin(client: AsyncClient):
    owner_token, org_id = await _signup_owner_and_create_org(client)
    admin_token = await _invite_and_accept(client, org_id, owner_token, role="admin")
    _member_token, member_user_id = await _invite_and_accept(
        client, org_id, owner_token, role="member", return_user_id=True
    )

    response = await client.patch(
        f"/api/v1/organizations/{org_id}/members/{member_user_id}",
        json={"role": "admin"},
        headers=_auth_headers(admin_token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_owner_can_promote_a_member_to_admin(client: AsyncClient):
    owner_token, org_id = await _signup_owner_and_create_org(client)
    _member_token, member_user_id = await _invite_and_accept(
        client, org_id, owner_token, role="member", return_user_id=True
    )

    response = await client.patch(
        f"/api/v1/organizations/{org_id}/members/{member_user_id}",
        json={"role": "admin"},
        headers=_auth_headers(owner_token),
    )
    assert response.status_code == 200
    assert response.json()["role"] == "admin"


@pytest.mark.asyncio
async def test_admin_cannot_remove_another_admin(client: AsyncClient):
    owner_token, org_id = await _signup_owner_and_create_org(client)
    admin_one_token = await _invite_and_accept(
        client, org_id, owner_token, role="admin"
    )
    _admin_two_token, admin_two_user_id = await _invite_and_accept(
        client, org_id, owner_token, role="admin", return_user_id=True
    )

    response = await client.delete(
        f"/api/v1/organizations/{org_id}/members",
        params={"user_id": admin_two_user_id},
        headers=_auth_headers(admin_one_token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_remove_a_member(client: AsyncClient):
    owner_token, org_id = await _signup_owner_and_create_org(client)
    admin_token = await _invite_and_accept(client, org_id, owner_token, role="admin")
    _member_token, member_user_id = await _invite_and_accept(
        client, org_id, owner_token, role="member", return_user_id=True
    )

    response = await client.delete(
        f"/api/v1/organizations/{org_id}/members",
        params={"user_id": member_user_id},
        headers=_auth_headers(admin_token),
    )
    # Single-member removal has always returned 200 (with a null body),
    # not 204 -- the route sets no explicit status_code (see
    # app/router/organizations.py's remove_member).
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_removed_member_loses_knowledge_access_and_organization_id(
    client: AsyncClient,
):
    """Removing a member must clear their stale KnowledgeAccess grant and
    User.organization_id, not just delete the OrganizationMember row --
    otherwise a future code path reading either of those directly (rather
    than going through OrganizationMember) would see a removed user as
    still belonging to the organization."""
    from app.database.models import KnowledgeAccess

    owner_token, org_id = await _signup_owner_and_create_org(client)
    _member_token, member_user_id = await _invite_and_accept(
        client, org_id, owner_token, role="member", return_user_id=True
    )

    response = await client.delete(
        f"/api/v1/organizations/{org_id}/members",
        params={"user_id": member_user_id},
        headers=_auth_headers(owner_token),
    )
    assert response.status_code == 200

    async with async_session_factory() as session:
        user = await session.get(User, member_user_id)
        assert user.organization_id is None

        access_result = await session.execute(
            select(KnowledgeAccess).where(
                KnowledgeAccess.user_id == member_user_id,
                KnowledgeAccess.organization_id == org_id,
            )
        )
        assert access_result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_owner_role_cannot_be_changed_or_removed_by_another_admin(
    client: AsyncClient,
):
    owner_token, org_id = await _signup_owner_and_create_org(client)
    owner_profile = (
        await client.get("/api/v1/users/me", headers=_auth_headers(owner_token))
    ).json()
    admin_token = await _invite_and_accept(client, org_id, owner_token, role="admin")

    demote_response = await client.patch(
        f"/api/v1/organizations/{org_id}/members/{owner_profile['id']}",
        json={"role": "member"},
        headers=_auth_headers(admin_token),
    )
    assert demote_response.status_code == 403

    remove_response = await client.delete(
        f"/api/v1/organizations/{org_id}/members",
        params={"user_id": owner_profile["id"]},
        headers=_auth_headers(admin_token),
    )
    assert remove_response.status_code == 403


@pytest.mark.asyncio
async def test_owner_knowledge_access_cannot_be_revoked(client: AsyncClient):
    owner_token, org_id = await _signup_owner_and_create_org(client)
    owner_profile = (
        await client.get("/api/v1/users/me", headers=_auth_headers(owner_token))
    ).json()

    revoke_response = await client.delete(
        f"/api/v1/organizations/{org_id}/knowledge-access",
        params={"user_id": owner_profile["id"]},
        headers=_auth_headers(owner_token),
    )
    assert revoke_response.status_code == 403


@pytest.mark.asyncio
async def test_member_invited_gets_default_company_knowledge_access(
    client: AsyncClient,
):
    """New behavior (spec change from the old opt-in model): a Member gets
    company-knowledge access by default at accept time, revocable by an
    Owner/Admin afterward -- not an opt-in grant an admin must remember to
    make."""
    owner_token, org_id = await _signup_owner_and_create_org(client)
    _member_token, member_user_id = await _invite_and_accept(
        client, org_id, owner_token, role="member", return_user_id=True
    )

    members = (
        await client.get(
            f"/api/v1/organizations/{org_id}/members",
            headers=_auth_headers(owner_token),
        )
    ).json()["items"]
    member = next(m for m in members if m["user_id"] == member_user_id)
    assert member["has_company_access"] is True


@pytest.mark.asyncio
async def test_standalone_list_knowledge_access_endpoint_was_removed(
    client: AsyncClient,
):
    """The GET .../knowledge-access list endpoint duplicated data
    list_members already carries per-member (has_company_access) and had
    no caller (frontend or test) that actually used it as a GET -- removed
    in favor of the single list_members response. The path still has
    POST/DELETE registered, so a GET against it is a 405, not a 404."""
    owner_token, org_id = await _signup_owner_and_create_org(client)

    response = await client.get(
        f"/api/v1/organizations/{org_id}/knowledge-access",
        headers=_auth_headers(owner_token),
    )
    assert response.status_code == 405


@pytest.mark.asyncio
async def test_admin_and_member_can_upload_or_be_denied_company_documents(
    client: AsyncClient, tmp_path
):
    owner_token, org_id = await _signup_owner_and_create_org(client)
    _member_token = await _invite_and_accept(client, org_id, owner_token, role="member")

    with patch(
        "app.service.document_service.get_storage_provider",
        return_value=LocalFilesystemStorageProvider(tmp_path),
    ):
        member_upload = await client.post(
            f"/api/v1/organizations/{org_id}/documents",
            files={"file": ("notes.txt", b"hello", "text/plain")},
            headers=_auth_headers(_member_token),
        )
        assert member_upload.status_code == 403

        owner_upload = await client.post(
            f"/api/v1/organizations/{org_id}/documents",
            files={"file": ("notes.txt", b"hello from the owner", "text/plain")},
            headers=_auth_headers(owner_token),
        )
        assert owner_upload.status_code == 200
        document_id = owner_upload.json()["id"]

        async with async_session_factory() as session:
            db_document = await session.get(Document, document_id)
            db_document.status = "completed"
            await session.commit()

        delete_response = await client.delete(
            f"/api/v1/organizations/{org_id}/documents/{document_id}",
            headers=_auth_headers(owner_token),
        )
        assert delete_response.status_code == 204


# --- Invitation router endpoints (Task 5) ------------------------------------


@pytest.mark.asyncio
async def test_admin_can_invite_a_member_but_not_an_admin(client: AsyncClient):
    owner_token, org_id = await _signup_owner_and_create_org(client)
    admin_token = await _invite_and_accept(client, org_id, owner_token, role="admin")

    with patch("app.service.invitation_service.send_email", new=AsyncMock()):
        member_invite = await client.post(
            f"/api/v1/organizations/{org_id}/invitations",
            data={
                "email": f"{PREFIX}-newmember@{PREFIX}.example.com",
                "role": "member",
            },
            headers=_auth_headers(admin_token),
        )
        admin_invite = await client.post(
            f"/api/v1/organizations/{org_id}/invitations",
            data={"email": f"{PREFIX}-newadmin@{PREFIX}.example.com", "role": "admin"},
            headers=_auth_headers(admin_token),
        )
    assert member_invite.status_code == 200
    assert admin_invite.status_code == 403


@pytest.mark.asyncio
async def test_create_invitation_requires_either_email_or_a_file(
    client: AsyncClient,
):
    owner_token, org_id = await _signup_owner_and_create_org(client)

    response = await client.post(
        f"/api/v1/organizations/{org_id}/invitations",
        headers=_auth_headers(owner_token),
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_invitation_rejects_both_email_and_a_file(client: AsyncClient):
    owner_token, org_id = await _signup_owner_and_create_org(client)
    csv_content = f"email,role\n{PREFIX}-both@{PREFIX}.example.com,member\n".encode()

    response = await client.post(
        f"/api/v1/organizations/{org_id}/invitations",
        data={"email": f"{PREFIX}-both@{PREFIX}.example.com", "role": "member"},
        files={"file": ("employees.csv", csv_content, "text/csv")},
        headers=_auth_headers(owner_token),
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_list_invitations_requires_admin_or_owner(client: AsyncClient):
    owner_token, org_id = await _signup_owner_and_create_org(client)
    _member_token, _uid = await _invite_and_accept(
        client, org_id, owner_token, role="member", return_user_id=True
    )
    member_token = _member_token

    response = await client.get(
        f"/api/v1/organizations/{org_id}/invitations",
        headers=_auth_headers(member_token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_revoke_invitation(client: AsyncClient):
    owner_token, org_id = await _signup_owner_and_create_org(client)
    with patch("app.service.invitation_service.send_email", new=AsyncMock()):
        invite = await client.post(
            f"/api/v1/organizations/{org_id}/invitations",
            data={"email": f"{PREFIX}-revokee@{PREFIX}.example.com", "role": "member"},
            headers=_auth_headers(owner_token),
        )
    invitation_id = invite.json()["id"]

    response = await client.delete(
        f"/api/v1/organizations/{org_id}/invitations/{invitation_id}",
        headers=_auth_headers(owner_token),
    )
    assert response.status_code == 204

    listing = await client.get(
        f"/api/v1/organizations/{org_id}/invitations",
        headers=_auth_headers(owner_token),
    )
    revoked = next(i for i in listing.json() if i["id"] == invitation_id)
    assert revoked["status"] == "revoked"


# --- Bulk CSV/Excel invitation upload (Task 7) -------------------------------


@pytest.mark.asyncio
async def test_bulk_upload_succeeds_when_every_row_is_valid(client: AsyncClient):
    owner_token, org_id = await _signup_owner_and_create_org(client)
    csv_content = (
        "email,role\n"
        f"{PREFIX}-bulk1@{PREFIX}.example.com,member\n"
        f"{PREFIX}-bulk2@{PREFIX}.example.com,admin\n"  # fine -- owner uploading
    ).encode()

    with patch("app.service.invitation_service.send_email", new=AsyncMock()):
        response = await client.post(
            f"/api/v1/organizations/{org_id}/invitations",
            files={"file": ("employees.csv", csv_content, "text/csv")},
            headers=_auth_headers(owner_token),
        )

    assert response.status_code == 200
    body = response.json()
    assert len(body["succeeded"]) == 2
    assert len(body["failed"]) == 0


@pytest.mark.asyncio
async def test_bulk_upload_rejects_the_entire_file_if_any_row_is_invalid(
    client: AsyncClient,
):
    """A file with even one invalid row is rejected wholesale -- no
    invitations are created for ANY row, valid or not."""
    owner_token, org_id = await _signup_owner_and_create_org(client)
    valid_email = f"{PREFIX}-bulk-valid@{PREFIX}.example.com"
    csv_content = (
        f"email,role\n{valid_email},member\nnot-an-email,member\n"
    ).encode()

    with patch("app.service.invitation_service.send_email", new=AsyncMock()):
        response = await client.post(
            f"/api/v1/organizations/{org_id}/invitations",
            files={"file": ("employees.csv", csv_content, "text/csv")},
            headers=_auth_headers(owner_token),
        )

    assert response.status_code == 422
    assert "row 2" in response.text.lower()

    listing = await client.get(
        f"/api/v1/organizations/{org_id}/invitations",
        headers=_auth_headers(owner_token),
    )
    assert not any(i["email"] == valid_email for i in listing.json())


@pytest.mark.asyncio
async def test_bulk_upload_rejects_the_entire_file_for_an_invalid_role(
    client: AsyncClient,
):
    owner_token, org_id = await _signup_owner_and_create_org(client)
    csv_content = (
        f"email,role\n{PREFIX}-bulkrole@{PREFIX}.example.com,superadmin\n"
    ).encode()

    response = await client.post(
        f"/api/v1/organizations/{org_id}/invitations",
        files={"file": ("employees.csv", csv_content, "text/csv")},
        headers=_auth_headers(owner_token),
    )

    assert response.status_code == 422
    assert "role" in response.text.lower()


@pytest.mark.asyncio
async def test_bulk_upload_rejects_the_entire_file_for_a_mismatched_domain(
    client: AsyncClient,
):
    owner_token, org_id = await _signup_owner_and_create_org(client)
    csv_content = b"email,role\nsomeone@othercompany.example.com,member\n"

    response = await client.post(
        f"/api/v1/organizations/{org_id}/invitations",
        files={"file": ("employees.csv", csv_content, "text/csv")},
        headers=_auth_headers(owner_token),
    )

    assert response.status_code == 422
    assert "domain" in response.text.lower()


@pytest.mark.asyncio
async def test_bulk_upload_rejects_the_entire_file_for_admin_rows_from_an_admin(
    client: AsyncClient,
):
    owner_token, org_id = await _signup_owner_and_create_org(client)
    admin_token = await _invite_and_accept(client, org_id, owner_token, role="admin")

    csv_content = (
        f"email,role\n{PREFIX}-bulkadmin@{PREFIX}.example.com,admin\n"
    ).encode()
    response = await client.post(
        f"/api/v1/organizations/{org_id}/invitations",
        files={"file": ("employees.csv", csv_content, "text/csv")},
        headers=_auth_headers(admin_token),
    )

    assert response.status_code == 422
    assert "admin" in response.text.lower()


# --- Bulk upload row parsing (unit-level, no HTTP/owner-signup needed) -------


def test_parse_bulk_rows_from_csv():
    from app.router.organizations import _parse_bulk_rows

    content = b"email,role\nalice@example.com,member\nbob@example.com,admin\n"
    rows = _parse_bulk_rows("employees.csv", content)
    assert rows == [
        (1, "alice@example.com", "member"),
        (2, "bob@example.com", "admin"),
    ]


def test_parse_bulk_rows_rejects_csv_missing_required_columns():
    from app.router.organizations import _parse_bulk_rows

    content = b"name,title\nalice,engineer\n"
    with pytest.raises(ValueError, match="email.*role"):
        _parse_bulk_rows("employees.csv", content)


def test_parse_bulk_rows_rejects_more_than_fifty_rows():
    from app.router.organizations import _parse_bulk_rows

    rows = "email,role\n" + "".join(
        f"user{i}@example.com,member\n" for i in range(51)
    )
    with pytest.raises(ValueError, match="50"):
        _parse_bulk_rows("employees.csv", rows.encode())


def test_parse_bulk_rows_accepts_exactly_fifty_rows():
    from app.router.organizations import _parse_bulk_rows

    rows = "email,role\n" + "".join(
        f"user{i}@example.com,member\n" for i in range(50)
    )
    assert len(_parse_bulk_rows("employees.csv", rows.encode())) == 50


def test_parse_bulk_rows_rejects_unsupported_file_type():
    from app.router.organizations import _parse_bulk_rows

    with pytest.raises(ValueError, match="Unsupported file type"):
        _parse_bulk_rows("employees.pdf", b"whatever")


def test_parse_bulk_rows_from_xlsx():
    import io

    import openpyxl

    from app.router.organizations import _parse_bulk_rows

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["email", "role"])
    sheet.append(["alice@example.com", "member"])
    buffer = io.BytesIO()
    workbook.save(buffer)

    rows = _parse_bulk_rows("employees.xlsx", buffer.getvalue())
    assert rows == [(1, "alice@example.com", "member")]


# --- Invitation email-delivery failure handling (Task 16 finding) -----------


@pytest.mark.asyncio
async def test_create_invitation_returns_502_when_email_delivery_fails(
    client: AsyncClient,
):
    owner_token, org_id = await _signup_owner_and_create_org(client)

    with patch(
        "app.service.invitation_service.send_email",
        new=AsyncMock(side_effect=OSError("smtp unreachable")),
    ):
        response = await client.post(
            f"/api/v1/organizations/{org_id}/invitations",
            data={
                "email": f"{PREFIX}-delivery-fail@{PREFIX}.example.com",
                "role": "member",
            },
            headers=_auth_headers(owner_token),
        )

    assert response.status_code == 502

    # The invitation still exists (pending) despite the 502, so Resend
    # can recover it without the Owner re-entering the email.
    listing = await client.get(
        f"/api/v1/organizations/{org_id}/invitations",
        headers=_auth_headers(owner_token),
    )
    emails = [i["email"] for i in listing.json()]
    assert f"{PREFIX}-delivery-fail@{PREFIX}.example.com" in emails


@pytest.mark.asyncio
async def test_bulk_upload_reports_email_delivery_failure_as_a_row_failure(
    client: AsyncClient,
):
    owner_token, org_id = await _signup_owner_and_create_org(client)
    csv_content = (
        f"email,role\n{PREFIX}-bulk-delivery-fail@{PREFIX}.example.com,member\n"
    ).encode()

    with patch(
        "app.service.invitation_service.send_email",
        new=AsyncMock(side_effect=OSError("smtp unreachable")),
    ):
        response = await client.post(
            f"/api/v1/organizations/{org_id}/invitations",
            files={"file": ("employees.csv", csv_content, "text/csv")},
            headers=_auth_headers(owner_token),
        )

    assert response.status_code == 200
    body = response.json()
    assert len(body["succeeded"]) == 0
    assert len(body["failed"]) == 1
    assert "email" in body["failed"][0]["reason"].lower()


@pytest.mark.asyncio
async def test_list_invitations_includes_accepted_at_once_accepted(client: AsyncClient):
    owner_token, org_id = await _signup_owner_and_create_org(client)
    _member_token, _uid = await _invite_and_accept(
        client, org_id, owner_token, role="member", return_user_id=True
    )

    listing = await client.get(
        f"/api/v1/organizations/{org_id}/invitations",
        headers=_auth_headers(owner_token),
    )
    accepted = next(i for i in listing.json() if i["status"] == "accepted")
    assert accepted["accepted_at"] is not None
