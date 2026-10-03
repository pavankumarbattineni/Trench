"""Shared FastAPI dependencies used across routers.

The tenant-role dependencies are all join-free: a user's tenant and role
live directly on the already-loaded `User` (`tenant_id` + `role`), so each
just checks those against the `tenant_id` path parameter (plus a tenant
existence lookup, so a nonexistent tenant is a 404 rather than a 403).
Each resolves to the caller's own `User`.
"""

import uuid

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.database.session import get_db
from app.service.auth_service import AuthService
from app.service.knowledge_access_service import KnowledgeAccessService
from app.service.tenant_service import TenantService

_COMPANY_ACCESS_DENIED = HTTPException(
    status.HTTP_403_FORBIDDEN,
    "You don't have access to this tenant's company knowledge",
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


async def require_tenant_admin_or_owner(
    tenant_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolves the caller iff they're an Admin or the Owner of
    `tenant_id`.

    Raises:
        HTTPException: 404 if the tenant doesn't exist, 403 if the caller
            is neither an Admin nor the Owner of it.
    """
    return await TenantService.require_admin_or_owner(
        db, user=current_user, tenant_id=tenant_id
    )


async def require_tenant_owner(
    tenant_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolves the caller iff they're specifically the Owner of
    `tenant_id` (not just any Admin).

    Raises:
        HTTPException: 404 if the tenant doesn't exist, 403 if the caller
            isn't the Owner of it.
    """
    return await TenantService.require_owner(
        db, user=current_user, tenant_id=tenant_id
    )


async def require_tenant_member(
    tenant_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolves the caller iff they belong to `tenant_id`, admin or not --
    for read-only endpoints any member should reach (e.g. the member
    roster), unlike admin-only mutations.

    Raises:
        HTTPException: 404 if the tenant doesn't exist, 403 if the caller
            doesn't belong to it.
    """
    return await TenantService.require_member(
        db, user=current_user, tenant_id=tenant_id
    )


async def require_company_knowledge_access(
    tenant_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> uuid.UUID:
    """Ensures the caller may read `tenant_id`'s company knowledge (an
    Admin/Owner, or a member explicitly granted access) -- never trusts
    the caller's own claim of which tenant they mean.

    Raises:
        HTTPException: 403 if the caller isn't authorized.
    """
    authorized_tenant_id = await KnowledgeAccessService.authorized_company_tenant_id(
        db, user_id=current_user.id
    )
    if authorized_tenant_id is None or authorized_tenant_id != tenant_id:
        raise _COMPANY_ACCESS_DENIED
    return tenant_id
