"""Membership management, company-knowledge access grants, and
company-document management for an organization.

Organization creation now happens only via POST /auth/signup/owner (see
app/router/auth.py) -- invitation-only onboarding means there is no
longer a self-service "create an organization" endpoint here.

Most mutations here (grant/revoke knowledge access, upload/delete a
company document, remove a Member) require the caller to be an Admin or
the Owner of the organization in question -- enforced by the
`require_org_admin_or_owner` dependency, never by trusting a frontend's
decision to hide a button. A smaller set (promoting a Member to Admin,
removing an Admin, bulk-removing every member) is Owner-only, enforced by
`require_org_owner` or an explicit role check. Document management
(upload/delete) has no delegation path -- a member can only ever be
granted read/query access via `knowledge-access`, never the ability to
manage documents.

Knowledge-access grant/revoke and member removal both take their target as
a query parameter (not a path segment), and both support a bulk "apply to
everyone" mode alongside the single-target one:

- `POST .../knowledge-access?user_id=...&allow_access=true|false` grants/
  revokes one member; `POST .../knowledge-access?access_all=true` grants
  every current member at once.
- `DELETE .../knowledge-access?user_id=...` revokes one member;
  `DELETE .../knowledge-access?remove_access=true` bulk-revokes every
  member's grant at once. Org admins are unaffected by any of these --
  their access comes from their role, not a grant row.
- `DELETE .../members?user_id=...` removes one member;
  `DELETE .../members?remove_all=true` removes every member except the
  permanent owner and the caller themself.

Each of these four bulk/single pairs rejects a request that gives neither
target, and rejects one that gives both (ambiguous) with a 422.
"""

import csv
import io
import uuid

import openpyxl
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Document,
    Invitation,
    Organization,
    OrganizationMember,
    User,
)
from app.database.session import get_db
from app.router.deps import (
    get_current_organization_membership,
    get_current_user,
    require_company_knowledge_access,
    require_org_admin_or_owner,
    require_org_member,
)
from app.schemas.document import DocumentListResponse, DocumentResponse
from app.schemas.invitation import (
    BulkInvitationResult,
    BulkInvitationRowError,
    InvitationResponse,
)
from app.schemas.organization import (
    KnowledgeAccessBulkGrantResponse,
    KnowledgeAccessBulkRevokeResponse,
    MemberBulkRemoveResponse,
    MyOrganizationResponse,
    OrganizationMemberResponse,
    PaginatedMembersResponse,
    UpdateMemberRoleRequest,
)
from app.service.document_service import DocumentService
from app.service.invitation_service import (
    DomainMismatchError,
    InvitationService,
    TooManyRoleError,
)
from app.service.knowledge_access_service import KnowledgeAccessService
from app.service.organization_service import OrganizationService
from app.service.user_service import UserService

router = APIRouter(prefix="/organizations", tags=["organizations"])

_USER_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
_email_adapter = TypeAdapter(EmailStr)


@router.get("/me", response_model=MyOrganizationResponse)
async def get_my_organization(
    membership: OrganizationMember = Depends(get_current_organization_membership),
    db: AsyncSession = Depends(get_db),
):
    """Returns the caller's own organization, plus their role in it.

    Args:
        membership: The caller's own organization membership (resolves to
            a 404 if they don't belong to one).
        db: An active async SQLAlchemy session.

    Returns:
        The organization the caller belongs to, and their role in it (so
        the frontend can decide whether to show admin-only controls).

    Raises:
        HTTPException: 404 if the caller doesn't belong to an organization.
    """
    organization = await OrganizationService.get_by_id(db, membership.organization_id)
    if organization is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    return MyOrganizationResponse(
        id=organization.id,
        name=organization.name,
        domain=organization.domain,
        owner_user_id=organization.owner_user_id,
        is_active=organization.is_active,
        created_at=organization.created_at,
        my_role=membership.role,
    )


async def _member_response(
    db: AsyncSession, member: OrganizationMember
) -> OrganizationMemberResponse:
    user = await UserService.get_by_id(db, member.user_id)
    organization = await OrganizationService.get_by_id(db, member.organization_id)
    has_access = await KnowledgeAccessService.has_company_access(
        db, user_id=member.user_id, organization_id=member.organization_id
    )
    return OrganizationMemberResponse(
        id=member.id,
        user_id=member.user_id,
        username=user.username,
        email=user.email,
        role=member.role,
        has_company_access=has_access or member.role in ("admin", "owner"),
        is_owner=organization is not None and organization.owner_user_id == user.id,
        created_at=member.created_at,
    )


@router.get("/{organization_id}/members", response_model=PaginatedMembersResponse)
async def list_members(
    organization_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    search: str | None = Query(default=None, max_length=128),
    _member: OrganizationMember = Depends(require_org_member),
    db: AsyncSession = Depends(get_db),
):
    """Lists an organization's members, paginated and optionally filtered.
    Any member can view the roster; only admins can manage it (add/remove/
    change roles -- see the other endpoints below).

    Args:
        organization_id: The organization whose members to list.
        page: 1-indexed page number (default 1).
        page_size: Members per page (default 10, max 100).
        search: Optional case-insensitive substring matched against a
            member's username or email.
        _member: The caller's own membership (only used to enforce that
            they belong to this organization).
        db: An active async SQLAlchemy session.

    Returns:
        The requested page of matching members (role and current
        company-knowledge-access status each), plus paging metadata.

    Raises:
        HTTPException: 404 if the organization doesn't exist; 403 if the
            caller doesn't belong to it.
    """
    members, total = await OrganizationService.list_members(
        db, organization_id, page=page, page_size=page_size, search=search
    )
    return PaginatedMembersResponse(
        items=[await _member_response(db, member) for member in members],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, -(-total // page_size)),
    )


@router.patch(
    "/{organization_id}/members/{user_id}", response_model=OrganizationMemberResponse
)
async def update_member_role(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    body: UpdateMemberRoleRequest,
    acting_membership: OrganizationMember = Depends(require_org_admin_or_owner),
    db: AsyncSession = Depends(get_db),
):
    """Promotes/demotes a member's role. Owner only.

    Args:
        organization_id: The organization the member belongs to.
        user_id: The member whose role to change.
        body: The new role ("admin" | "member").
        acting_membership: The caller's own membership (used to enforce
            the Owner-only requirement).
        db: An active async SQLAlchemy session.

    Returns:
        The updated member record.

    Raises:
        HTTPException: 404 if the organization or member doesn't exist;
            403 if the caller isn't the Owner of it, or if `user_id` is
            the organization's owner (whose role can never be changed).
    """
    member = await OrganizationService.update_role(
        db,
        organization_id=organization_id,
        target_user_id=user_id,
        role=body.role,
        acting_membership=acting_membership,
    )
    return await _member_response(db, member)


@router.delete(
    "/{organization_id}/members",
    response_model=OrganizationMemberResponse | MemberBulkRemoveResponse | None,
)
async def remove_member(
    organization_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    remove_all: bool = False,
    acting_membership: OrganizationMember = Depends(require_org_admin_or_owner),
    db: AsyncSession = Depends(get_db),
):
    """Removes a member from the organization -- either one (`user_id`),
    or every member at once (`remove_all=true`). Admin or Owner for a
    single Member; Owner only for removing an Admin or for the bulk
    `remove_all` mode.

    Args:
        organization_id: The organization to remove member(s) from.
        user_id: The single member to remove, when `remove_all` is false.
            Must be omitted when `remove_all` is true.
        remove_all: `true` removes every member except the permanent
            owner and the caller themself. Owner only.
        acting_membership: The caller's own membership.
        db: An active async SQLAlchemy session.

    Returns:
        Nothing (204) in single-member mode; how many members were
        removed in bulk mode.

    Raises:
        HTTPException: 422 if both/neither `user_id` and `remove_all` are
            meaningfully set; 404 if the organization or (single-member
            mode) member doesn't exist; 403 if the caller isn't an Admin
            or Owner of it, if `remove_all` is requested by a non-Owner,
            if (single-member mode) `user_id` is the organization's owner
            (who can never be removed), or if an Admin tries to remove
            another Admin (Owner only); 409 if the caller tries to remove
            themself in single-member mode.
    """
    if remove_all:
        if acting_membership.role != "owner":
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Only the organization owner can remove all members at once",
            )
        if user_id is not None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Provide either user_id or remove_all=true, not both",
            )
        removed_count = await OrganizationService.remove_all_members(
            db,
            organization_id=organization_id,
            acting_user_id=acting_membership.user_id,
        )
        return MemberBulkRemoveResponse(removed_count=removed_count)

    if user_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "user_id is required unless remove_all=true",
        )
    await OrganizationService.remove_member(
        db,
        organization_id=organization_id,
        target_user_id=user_id,
        acting_membership=acting_membership,
    )
    return None


@router.post(
    "/{organization_id}/knowledge-access",
    response_model=OrganizationMemberResponse | KnowledgeAccessBulkGrantResponse,
)
async def update_knowledge_access(
    organization_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    allow_access: bool | None = None,
    access_all: bool = False,
    _admin: OrganizationMember = Depends(require_org_admin_or_owner),
    db: AsyncSession = Depends(get_db),
):
    """Grants or revokes one member's company-knowledge access (depending
    on `allow_access`), or grants every current member at once
    (`access_all=true`). Admin only. Idempotent either way -- granting an
    already-granted user, or revoking a never-granted one, both succeed.

    Args:
        organization_id: The organization whose company knowledge access
            to change.
        user_id: The single member to change, when `access_all` is false.
            Must be omitted when `access_all` is true.
        allow_access: `true` to grant, `false` to revoke -- required
            (and only meaningful) in single-member mode.
        access_all: `true` bulk-grants every current member of the
            organization at once. There's no bulk *grant-with-false*
            equivalent -- use `DELETE .../knowledge-access?remove_access=true`
            to revoke everyone's access instead.
        _admin: The caller's admin membership (only used to enforce the
            admin-only requirement).
        db: An active async SQLAlchemy session.

    Returns:
        In single-member mode, that member's updated record; in bulk
        mode, how many members were newly granted access.

    Raises:
        HTTPException: 422 if both/neither `user_id` and `access_all` are
            meaningfully set, or if single-member mode is missing
            `allow_access`; 404 if the organization doesn't exist, or
            (single-member mode) `user_id` isn't a member of it; 403 if
            the caller isn't an admin of it, or (single-member mode)
            `user_id` is the organization's owner.
    """
    if access_all:
        if user_id is not None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Provide either user_id or access_all=true, not both",
            )
        granted_count = await KnowledgeAccessService.grant_all(
            db, organization_id=organization_id
        )
        return KnowledgeAccessBulkGrantResponse(granted_count=granted_count)

    if user_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "user_id is required unless access_all=true",
        )
    if allow_access is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "allow_access is required when user_id is given",
        )
    await OrganizationService.require_not_owner(
        db, organization_id=organization_id, target_user_id=user_id
    )
    if allow_access:
        await KnowledgeAccessService.grant(
            db, organization_id=organization_id, user_id=user_id
        )
    else:
        await KnowledgeAccessService.revoke(
            db, organization_id=organization_id, user_id=user_id
        )
    member = await OrganizationService.get_member(
        db, organization_id=organization_id, user_id=user_id
    )
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    return await _member_response(db, member)


@router.delete(
    "/{organization_id}/knowledge-access",
    response_model=OrganizationMemberResponse | KnowledgeAccessBulkRevokeResponse,
)
async def revoke_knowledge_access(
    organization_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    remove_access: bool = False,
    _admin: OrganizationMember = Depends(require_org_admin_or_owner),
    db: AsyncSession = Depends(get_db),
):
    """Revokes company-knowledge access -- either for one member, or (with
    `remove_access=true`) for every member of the organization at once.
    Admin only.

    Args:
        organization_id: The organization whose company knowledge access
            to revoke.
        user_id: The single member to revoke, when `remove_access` is
            false. Must be omitted when `remove_access` is true.
        remove_access: `true` bulk-revokes every member's grant for this
            organization (org admins are unaffected -- their access comes
            from their role, not a grant row).
        _admin: The caller's admin membership (only used to enforce the
            admin-only requirement).
        db: An active async SQLAlchemy session.

    Returns:
        In single-user mode, that member's updated record; in bulk mode,
        how many grants were revoked.

    Raises:
        HTTPException: 422 if both/neither `user_id` and `remove_access`
            are meaningfully set; 404 if the organization doesn't exist,
            or (single-user mode) `user_id` isn't a member of it; 403 if
            the caller isn't an admin of it, or (single-user mode)
            `user_id` is the organization's owner.
    """
    if remove_access:
        if user_id is not None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Provide either user_id or remove_access=true, not both",
            )
        revoked_count = await KnowledgeAccessService.revoke_all(
            db, organization_id=organization_id
        )
        return KnowledgeAccessBulkRevokeResponse(revoked_count=revoked_count)

    if user_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "user_id is required unless remove_access=true",
        )
    await OrganizationService.require_not_owner(
        db, organization_id=organization_id, target_user_id=user_id
    )
    await KnowledgeAccessService.revoke(
        db, organization_id=organization_id, user_id=user_id
    )
    member = await OrganizationService.get_member(
        db, organization_id=organization_id, user_id=user_id
    )
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    return await _member_response(db, member)


@router.post("/{organization_id}/documents", response_model=DocumentResponse)
async def upload_company_document(
    organization_id: uuid.UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    admin: OrganizationMember = Depends(require_org_admin_or_owner),
    db: AsyncSession = Depends(get_db),
) -> Document:
    """Uploads a company-knowledge document. Admin only -- document
    management has no delegation path; a member can only ever be granted
    read/query access (see knowledge-access above).

    Args:
        organization_id: The organization this document belongs to.
        file: The document file (PDF, DOCX, TXT, or Markdown).
        current_user: The authenticated admin performing the upload.
        admin: The caller's admin membership (only used to enforce the
            admin-only requirement).
        db: An active async SQLAlchemy session.

    Returns:
        The created (or, if this exact file was already uploaded for this
        organization, the existing) document.

    Raises:
        HTTPException: 422 on an invalid file; 404 if the organization
            doesn't exist; 403 if the caller isn't an admin of it; 409 if
            the admin already has a document pending/processing.
    """
    content = await file.read()
    return await DocumentService.upload_document(
        db,
        user=current_user,
        filename=file.filename or "untitled",
        content=content,
        knowledge_type="company",
        organization_id=organization_id,
    )


@router.get("/{organization_id}/documents", response_model=DocumentListResponse)
async def list_company_documents(
    organization_id: uuid.UUID,
    _access: uuid.UUID = Depends(require_company_knowledge_access),
    db: AsyncSession = Depends(get_db),
) -> DocumentListResponse:
    """Lists an organization's company documents. Requires company-knowledge
    access (an admin, or a member explicitly granted it) -- members can
    view/list documents without being able to upload or delete them.

    Args:
        organization_id: The organization whose company documents to list.
        _access: The resolved, authorized organization_id (only used to
            enforce that the caller has company-knowledge access).
        db: An active async SQLAlchemy session.

    Returns:
        Every company document for the organization.

    Raises:
        HTTPException: 403 if the caller doesn't have company-knowledge
            access to this organization.
    """
    documents = await DocumentService.list_company_documents(
        db, organization_id=organization_id
    )
    return DocumentListResponse(documents=documents)


@router.delete(
    "/{organization_id}/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_company_document(
    organization_id: uuid.UUID,
    document_id: uuid.UUID,
    _admin: OrganizationMember = Depends(require_org_admin_or_owner),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Deletes a company document. Admin only.

    Args:
        organization_id: The organization the document belongs to.
        document_id: The document to delete.
        _admin: The caller's admin membership (only used to enforce the
            admin-only requirement).
        db: An active async SQLAlchemy session.

    Raises:
        HTTPException: 404 if the document doesn't exist or belongs to a
            different organization; 403 if the caller isn't an admin of
            it; 409 if the document is still pending/processing.
    """
    await DocumentService.delete_company_document(
        db, organization_id=organization_id, document_id=document_id
    )


def _invitation_response(invitation: Invitation) -> InvitationResponse:
    return InvitationResponse(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        status=invitation.status,
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
        accepted_at=invitation.accepted_at,
    )


@router.post(
    "/{organization_id}/invitations",
    response_model=InvitationResponse | BulkInvitationResult,
)
async def create_invitation(
    organization_id: uuid.UUID,
    email: str | None = Form(default=None),
    role: str = Form(default="member"),
    file: UploadFile | None = File(default=None),
    acting_membership: OrganizationMember = Depends(require_org_admin_or_owner),
    db: AsyncSession = Depends(get_db),
):
    """Invites one employee by email, or many at once from an uploaded
    CSV/XLSX file -- exactly one of `email` or `file` must be given.
    Admin or Owner. Admins may only set role="member"; only the Owner may
    invite as "admin" (single mode) or include admin rows (file mode).

    Args:
        organization_id: The organization to invite into.
        email: The single invitee's address. Mutually exclusive with
            `file`.
        role: The single invitee's role ("admin" | "member", default
            "member"). Ignored in file mode, where each row supplies its
            own role.
        file: A CSV/XLSX upload (columns: email, role) inviting up to
            MAX_BULK_INVITATION_ROWS people at once. Mutually exclusive
            with `email`.
        acting_membership: The caller's own membership.
        db: An active async SQLAlchemy session.

    Returns:
        In single mode, the created/reused invitation. In file mode, a
        BulkInvitationResult with a succeeded/failed entry per row --
        one invalid or disallowed row never blocks the others in the
        same file.

    Raises:
        HTTPException: 422 if neither or both of `email`/`file` are
            given, if `role` isn't "admin"/"member" (single mode), or if
            the file itself is unreadable, missing required columns, or
            has too many rows (a whole-file problem, distinct from a
            per-row one reported in the response body instead); 403 if
            the caller isn't an Admin/Owner of the organization, or (Admin
            caller, single mode) tries to set role="admin"; 404 if the
            organization doesn't exist; 502 if the invitation was created
            but the email itself couldn't be sent (single mode -- use the
            resend endpoint; file mode reports this per-row instead).
    """
    if (email is None) == (file is None):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Provide either email or file, not both/neither",
        )

    organization = await OrganizationService.get_by_id(db, organization_id)
    if organization is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")

    if file is not None:
        content = await file.read()
        try:
            rows = _parse_bulk_rows(file.filename or "", content)
            validated_rows = _validate_bulk_rows(
                rows, organization=organization, acting_membership=acting_membership
            )
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)
            ) from exc
        return await _bulk_create_invitations(
            db,
            organization=organization,
            acting_membership=acting_membership,
            rows=validated_rows,
        )

    if role not in ("admin", "member"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Invalid role: {role!r}"
        )
    try:
        validated_email = str(_email_adapter.validate_python(email))
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Invalid email address"
        ) from exc
    try:
        invitation, _raw_token = await InvitationService.create(
            db,
            organization=organization,
            inviter_membership=acting_membership,
            email=validated_email,
            role=role,
        )
    except TooManyRoleError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except DomainMismatchError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except InvitationService.EmailDeliveryError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return _invitation_response(invitation)


@router.get("/{organization_id}/invitations", response_model=list[InvitationResponse])
async def list_invitations(
    organization_id: uuid.UUID,
    _acting: OrganizationMember = Depends(require_org_admin_or_owner),
    db: AsyncSession = Depends(get_db),
):
    """Lists every invitation (pending, accepted, revoked, expired) for
    the organization. Admin or Owner."""
    invitations = await InvitationService.list_for_organization(db, organization_id)
    return [_invitation_response(i) for i in invitations]


async def _require_invitation_in_org(
    db: AsyncSession, *, organization_id: uuid.UUID, invitation_id: uuid.UUID
):
    invitation = await InvitationService.get_by_id(db, invitation_id)
    if invitation is None or invitation.organization_id != organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")
    return invitation


@router.post(
    "/{organization_id}/invitations/{invitation_id}/resend",
    response_model=InvitationResponse,
)
async def resend_invitation(
    organization_id: uuid.UUID,
    invitation_id: uuid.UUID,
    _acting: OrganizationMember = Depends(require_org_admin_or_owner),
    db: AsyncSession = Depends(get_db),
):
    """Rotates the token/expiry and re-sends a pending invitation's
    email. Admin or Owner.

    Raises:
        HTTPException: 404 if the invitation doesn't exist in this org;
            409 if it's no longer pending (already accepted/revoked/expired);
            502 if the token was refreshed but the email couldn't be sent.
    """
    invitation = await _require_invitation_in_org(
        db, organization_id=organization_id, invitation_id=invitation_id
    )
    if invitation.status != "pending":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Can't resend a {invitation.status} invitation",
        )
    organization = await OrganizationService.get_by_id(db, organization_id)
    try:
        await InvitationService.resend(
            db, invitation=invitation, organization=organization
        )
    except InvitationService.EmailDeliveryError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return InvitationResponse(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        status=invitation.status,
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
        accepted_at=invitation.accepted_at,
    )


@router.delete(
    "/{organization_id}/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_invitation(
    organization_id: uuid.UUID,
    invitation_id: uuid.UUID,
    _acting: OrganizationMember = Depends(require_org_admin_or_owner),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revokes a pending invitation. Admin or Owner.

    Raises:
        HTTPException: 404 if the invitation doesn't exist in this org.
    """
    invitation = await _require_invitation_in_org(
        db, organization_id=organization_id, invitation_id=invitation_id
    )
    await InvitationService.revoke(db, invitation=invitation)


MAX_BULK_INVITATION_ROWS = 50


def _parse_bulk_rows(filename: str, content: bytes) -> list[tuple[int, str, str]]:
    """Returns a list of (row_number, raw_email, raw_role) tuples, 1-indexed
    by data row (header excluded). Raises ValueError for an unsupported
    file type, missing required columns, or more than
    MAX_BULK_INVITATION_ROWS rows -- caught by the endpoint and turned into
    a 422, since each of these is a whole-file problem, not a per-row one.
    """
    rows = _parse_bulk_rows_unbounded(filename, content)
    if len(rows) > MAX_BULK_INVITATION_ROWS:
        raise ValueError(
            f"A single upload can invite at most {MAX_BULK_INVITATION_ROWS} "
            f"people at a time (got {len(rows)})"
        )
    return rows


def _parse_bulk_rows_unbounded(
    filename: str, content: bytes
) -> list[tuple[int, str, str]]:
    if filename.lower().endswith(".csv"):
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None or {"email", "role"} - {
            f.strip().lower() for f in reader.fieldnames
        }:
            raise ValueError("CSV must have 'email' and 'role' columns")
        return [
            (
                i + 1,
                (row.get("email") or "").strip(),
                (row.get("role") or "member").strip(),
            )
            for i, row in enumerate(reader)
        ]
    if filename.lower().endswith(".xlsx"):
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
        sheet = workbook.active
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            raise ValueError("Empty spreadsheet")
        header = [str(c).strip().lower() if c else "" for c in rows[0]]
        if "email" not in header or "role" not in header:
            raise ValueError("Spreadsheet must have 'email' and 'role' columns")
        email_idx, role_idx = header.index("email"), header.index("role")
        return [
            (
                i + 1,
                str(row[email_idx]).strip() if row[email_idx] else "",
                str(row[role_idx]).strip().lower()
                if role_idx < len(row) and row[role_idx]
                else "member",
            )
            for i, row in enumerate(rows[1:])
        ]
    raise ValueError("Unsupported file type -- upload a .csv or .xlsx file")


def _validate_bulk_rows(
    rows: list[tuple[int, str, str]],
    *,
    organization: Organization,
    acting_membership: OrganizationMember,
) -> list[tuple[int, str, str]]:
    """Validates every row's data up front -- email format, role value,
    inviter permission, and domain match -- the same checks
    InvitationService.create would otherwise make one row at a time. If
    any row fails, the whole file is rejected (ValueError, caught by the
    endpoint and turned into a 422) with every problem listed, and nothing
    is written to the database or sent by email. A row that fails only
    because of something InvitationService.create can't know in advance
    (e.g. an SMTP outage) is not a data-validity problem and is reported
    per-row in the response body instead, after this validation passes.
    """
    problems: list[str] = []
    validated: list[tuple[int, str, str]] = []
    for row_number, raw_email, raw_role in rows:
        try:
            email = str(_email_adapter.validate_python(raw_email))
        except ValidationError:
            problems.append(f"Row {row_number} ({raw_email}): Invalid email address")
            continue
        role = raw_role or "member"
        if role not in ("admin", "member"):
            problems.append(f"Row {row_number} ({raw_email}): Invalid role {role!r}")
            continue
        if acting_membership.role == "admin" and role != "member":
            problems.append(
                f"Row {row_number} ({email}): Admins may only invite members"
            )
            continue
        email_domain = email.rsplit("@", 1)[-1].lower()
        if email_domain != organization.domain.lower():
            problems.append(
                f"Row {row_number} ({email}): doesn't match this organization's "
                f"domain ({organization.domain})"
            )
            continue
        validated.append((row_number, email, role))

    if problems:
        raise ValueError(
            "The file contains invalid rows and was not processed: "
            + "; ".join(problems)
        )
    return validated


async def _bulk_create_invitations(
    db: AsyncSession,
    *,
    organization: Organization,
    acting_membership: OrganizationMember,
    rows: list[tuple[int, str, str]],
) -> BulkInvitationResult:
    """The file-upload branch of POST .../invitations, given rows that
    have already passed _validate_bulk_rows (the whole file is rejected
    before this is ever called if any row is invalid). Each row is still
    processed independently here so a runtime hiccup (e.g. an SMTP outage)
    for one row never blocks the others in the same file."""
    succeeded: list[InvitationResponse] = []
    failed: list[BulkInvitationRowError] = []
    for row_number, email, role in rows:
        try:
            invitation, _raw_token = await InvitationService.create(
                db,
                organization=organization,
                inviter_membership=acting_membership,
                email=email,
                role=role,
            )
        except TooManyRoleError as exc:
            failed.append(
                BulkInvitationRowError(row=row_number, email=email, reason=str(exc))
            )
            continue
        except DomainMismatchError as exc:
            failed.append(
                BulkInvitationRowError(row=row_number, email=email, reason=str(exc))
            )
            continue
        except InvitationService.EmailDeliveryError as exc:
            failed.append(
                BulkInvitationRowError(row=row_number, email=email, reason=str(exc))
            )
            continue
        succeeded.append(_invitation_response(invitation))

    return BulkInvitationResult(succeeded=succeeded, failed=failed)
