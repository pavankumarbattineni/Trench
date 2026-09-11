"""Shared FastAPI dependencies used across routers."""

import uuid

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import OrganizationMember, User
from app.database.session import get_db
from app.service.auth_service import AuthService
from app.service.knowledge_access_service import KnowledgeAccessService
from app.service.organization_service import OrganizationService

_COMPANY_ACCESS_DENIED = HTTPException(
    status.HTTP_403_FORBIDDEN,
    "You don't have access to this organization's company knowledge",
)

# A real FastAPI/OpenAPI security scheme (not just a raw header read) --
# this is what makes Swagger show a lock icon and an "Authorize" button on
# every route that depends on `get_current_user`, and adds the scheme to
# the OpenAPI `securitySchemes` component. `auto_error=False` lets
# `get_current_user` raise its own 401 (with a message) rather than
# FastAPI's generic one when the header is missing. Bearer-only, no
# cookies -- mirrors abyss_backend/abyss_frontend: the frontend stores its
# own access_token and sends `Authorization: Bearer <token>` itself.
_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolves the authenticated user from the Authorization: Bearer header.

    Args:
        credentials: The parsed Bearer credentials, if present.
        db: An active async SQLAlchemy session.

    Returns:
        The authenticated, active User.

    Raises:
        HTTPException: 401 if there is no valid, current access token.
    """
    token = credentials.credentials if credentials else None
    return await AuthService.resolve_access_token(db, token)


async def require_org_admin(
    organization_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrganizationMember:
    """Resolves the caller's admin membership for `organization_id`.

    Raises:
        HTTPException: 404 if the organization doesn't exist, 403 if the
            caller isn't an admin of it.
    """
    return await OrganizationService.require_admin(
        db, user=current_user, organization_id=organization_id
    )


async def require_org_member(
    organization_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrganizationMember:
    """Resolves the caller's membership for `organization_id`, admin or
    not -- for read-only endpoints any member should reach (e.g. the
    member roster), unlike admin-only mutations.

    Raises:
        HTTPException: 404 if the organization doesn't exist, 403 if the
            caller doesn't belong to it.
    """
    return await OrganizationService.require_member(
        db, user=current_user, organization_id=organization_id
    )


async def get_current_organization_membership(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrganizationMember:
    """Resolves the caller's own organization membership.

    Raises:
        HTTPException: 404 if the caller doesn't belong to an organization.
    """
    membership = await OrganizationService.get_membership_for_user(db, current_user.id)
    if membership is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "You don't belong to an organization"
        )
    return membership


async def require_company_knowledge_access(
    organization_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> uuid.UUID:
    """Ensures the caller may read `organization_id`'s company knowledge
    (an admin, or an explicit KnowledgeAccess grant) -- never trusts the
    caller's own claim of which organization they mean.

    Raises:
        HTTPException: 403 if the caller isn't authorized.
    """
    authorized_org_id = await KnowledgeAccessService.authorized_company_organization_id(
        db, user_id=current_user.id
    )
    if authorized_org_id is None or authorized_org_id != organization_id:
        raise _COMPANY_ACCESS_DENIED
    return organization_id
