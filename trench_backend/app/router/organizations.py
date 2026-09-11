"""Organization creation, membership management, company-knowledge access
grants, and company-document management.

Every mutation here (add/remove member, change role, grant/revoke
knowledge access, upload/delete a company document) requires the caller
to be an admin of the organization in question -- enforced by the
`require_org_admin` dependency, never by trusting a frontend's decision
to hide a button. Document management (upload/delete) is admin-only with
no delegation path -- a member can only ever be granted read/query access
via `knowledge-access`, never the ability to manage documents.
"""

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Document, OrganizationMember, User
from app.database.session import get_db
from app.router.deps import (
    get_current_organization_membership,
    get_current_user,
    require_company_knowledge_access,
    require_org_admin,
    require_org_member,
)
from app.schemas.document import DocumentListResponse, DocumentResponse
from app.schemas.organization import (
    AddMemberRequest,
    KnowledgeAccessResponse,
    MyOrganizationResponse,
    OrganizationCreatedResponse,
    OrganizationCreateRequest,
    OrganizationMemberResponse,
    UpdateMemberRoleRequest,
)
from app.service.document_service import DocumentService
from app.service.knowledge_access_service import KnowledgeAccessService
from app.service.organization_service import OrganizationService
from app.service.user_service import UserService

router = APIRouter(prefix="/organizations", tags=["organizations"])

_USER_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "User not found")


@router.post("", response_model=OrganizationCreatedResponse)
async def create_organization(
    body: OrganizationCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Creates an organization with the caller as its first admin and
    permanent owner.

    Args:
        body: The new organization's name.
        current_user: The authenticated user, who becomes its first admin
            and owner.
        db: An active async SQLAlchemy session.

    Returns:
        The newly created organization, plus how many existing users on
        the same email domain were automatically added as members. Its
        domain is derived from the creator's own email address, not
        supplied by the caller.

    Raises:
        HTTPException: 409 if the caller already belongs to an
            organization, if the name is already taken, or if an
            organization already exists for the caller's email domain;
            422 if the caller's email is a public/personal provider
            (Gmail, Yahoo, etc.) rather than a work domain.
    """
    organization, _member, auto_added = await OrganizationService.create(
        db, creator=current_user, name=body.name
    )
    return OrganizationCreatedResponse(
        id=organization.id,
        name=organization.name,
        domain=organization.domain,
        owner_user_id=organization.owner_user_id,
        is_active=organization.is_active,
        created_at=organization.created_at,
        auto_added_members=auto_added,
    )


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
        has_company_access=has_access or member.role == "admin",
        is_owner=organization is not None and organization.owner_user_id == user.id,
        created_at=member.created_at,
    )


@router.get(
    "/{organization_id}/members", response_model=list[OrganizationMemberResponse]
)
async def list_members(
    organization_id: uuid.UUID,
    _member: OrganizationMember = Depends(require_org_member),
    db: AsyncSession = Depends(get_db),
):
    """Lists an organization's members. Any member can view the roster;
    only admins can manage it (add/remove/change roles -- see the other
    endpoints below).

    Args:
        organization_id: The organization whose members to list.
        _member: The caller's own membership (only used to enforce that
            they belong to this organization).
        db: An active async SQLAlchemy session.

    Returns:
        Every member of the organization, each with their role and
        current company-knowledge-access status.

    Raises:
        HTTPException: 404 if the organization doesn't exist; 403 if the
            caller doesn't belong to it.
    """
    members = await OrganizationService.list_members(db, organization_id)
    return [await _member_response(db, member) for member in members]


@router.post("/{organization_id}/members", response_model=OrganizationMemberResponse)
async def add_member(
    organization_id: uuid.UUID,
    body: AddMemberRequest,
    _admin: OrganizationMember = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    """Adds an existing user to the organization. Admin only.

    Args:
        organization_id: The organization to add the user to.
        body: The target user's username and their initial role.
        _admin: The caller's admin membership (only used to enforce the
            admin-only requirement).
        db: An active async SQLAlchemy session.

    Returns:
        The new member record.

    Raises:
        HTTPException: 404 if the organization doesn't exist or no user
            has that username; 403 if the caller isn't an admin; 422 if
            that user's email domain doesn't match the organization's;
            409 if that user already belongs to an organization.
    """
    organization = await OrganizationService.get_by_id(db, organization_id)
    if organization is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    target_user = await UserService.get_by_username(db, body.username)
    if target_user is None:
        raise _USER_NOT_FOUND
    member = await OrganizationService.add_member(
        db,
        organization=organization,
        target_user=target_user,
        role=body.role,
    )
    return await _member_response(db, member)


@router.patch(
    "/{organization_id}/members/{user_id}", response_model=OrganizationMemberResponse
)
async def update_member_role(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    body: UpdateMemberRoleRequest,
    _admin: OrganizationMember = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    """Promotes/demotes a member's role. Admin only.

    Args:
        organization_id: The organization the member belongs to.
        user_id: The member whose role to change.
        body: The new role ("admin" | "member").
        _admin: The caller's admin membership (only used to enforce the
            admin-only requirement).
        db: An active async SQLAlchemy session.

    Returns:
        The updated member record.

    Raises:
        HTTPException: 404 if the organization or member doesn't exist;
            403 if the caller isn't an admin of it, or if `user_id` is
            the organization's owner (whose role can never be changed).
    """
    member = await OrganizationService.update_role(
        db, organization_id=organization_id, target_user_id=user_id, role=body.role
    )
    return await _member_response(db, member)


@router.delete(
    "/{organization_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_member(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    admin: OrganizationMember = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Removes a member from the organization. Admin only.

    Args:
        organization_id: The organization to remove the member from.
        user_id: The member to remove.
        admin: The caller's admin membership.
        db: An active async SQLAlchemy session.

    Raises:
        HTTPException: 404 if the organization or member doesn't exist;
            403 if the caller isn't an admin of it, or if `user_id` is
            the organization's owner (who can never be removed); 409 if
            an admin tries to remove themself (transfer admin to someone
            else first).
    """
    await OrganizationService.remove_member(
        db,
        organization_id=organization_id,
        target_user_id=user_id,
        acting_user_id=admin.user_id,
    )


@router.get(
    "/{organization_id}/knowledge-access", response_model=list[KnowledgeAccessResponse]
)
async def list_knowledge_access(
    organization_id: uuid.UUID,
    _admin: OrganizationMember = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    """Lists who currently has company-knowledge access. Admin only.

    Args:
        organization_id: The organization to list grants for.
        _admin: The caller's admin membership (only used to enforce the
            admin-only requirement).
        db: An active async SQLAlchemy session.

    Returns:
        Every active company-knowledge-access grant for the organization.

    Raises:
        HTTPException: 404 if the organization doesn't exist; 403 if the
            caller isn't an admin of it.
    """
    grants = await KnowledgeAccessService.list_for_organization(db, organization_id)
    responses = []
    for grant in grants:
        user = await UserService.get_by_id(db, grant.user_id)
        responses.append(
            KnowledgeAccessResponse(
                id=grant.id,
                user_id=grant.user_id,
                username=user.username,
                knowledge_type=grant.knowledge_type,
                is_active=grant.is_active,
                created_at=grant.created_at,
            )
        )
    return responses


@router.post(
    "/{organization_id}/knowledge-access/{user_id}",
    response_model=KnowledgeAccessResponse,
)
async def grant_knowledge_access(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    _admin: OrganizationMember = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    """Grants a member access to the organization's company knowledge. Admin only.

    Args:
        organization_id: The organization whose company knowledge to grant
            access to.
        user_id: The member being granted access.
        _admin: The caller's admin membership (only used to enforce the
            admin-only requirement).
        db: An active async SQLAlchemy session.

    Returns:
        The new (or reactivated) access grant.

    Raises:
        HTTPException: 404 if the organization doesn't exist; 403 if the
            caller isn't an admin of it, or if `user_id` is the
            organization's owner (who always has access regardless).
    """
    await OrganizationService.require_not_owner(
        db, organization_id=organization_id, target_user_id=user_id
    )
    grant = await KnowledgeAccessService.grant(
        db, organization_id=organization_id, user_id=user_id
    )
    user = await UserService.get_by_id(db, user_id)
    return KnowledgeAccessResponse(
        id=grant.id,
        user_id=grant.user_id,
        username=user.username,
        knowledge_type=grant.knowledge_type,
        is_active=grant.is_active,
        created_at=grant.created_at,
    )


@router.delete(
    "/{organization_id}/knowledge-access/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_knowledge_access(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    _admin: OrganizationMember = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revokes a member's company-knowledge access. Admin only.

    Args:
        organization_id: The organization whose company knowledge access
            to revoke.
        user_id: The member whose access to revoke.
        _admin: The caller's admin membership (only used to enforce the
            admin-only requirement).
        db: An active async SQLAlchemy session.

    Raises:
        HTTPException: 404 if the organization doesn't exist or the user
            has no such grant; 403 if the caller isn't an admin of it, or
            if `user_id` is the organization's owner (whose access can
            never be revoked).
    """
    await OrganizationService.require_not_owner(
        db, organization_id=organization_id, target_user_id=user_id
    )
    await KnowledgeAccessService.revoke(
        db, organization_id=organization_id, user_id=user_id
    )


@router.post("/{organization_id}/documents", response_model=DocumentResponse)
async def upload_company_document(
    organization_id: uuid.UUID,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    admin: OrganizationMember = Depends(require_org_admin),
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
    _admin: OrganizationMember = Depends(require_org_admin),
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
