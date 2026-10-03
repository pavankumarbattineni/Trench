"""BYOK credential management -- a single API for both personal and
tenant-scoped credentials, selected by the optional `tenant_id` on each
request: given, it acts on the tenant's shared credential (caller must be
its Owner); omitted, it acts on the caller's own personal credential.
There is no separate tenant-credentials API (and, since both live in the
one `credentials` table, no separate service either).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.database.session import get_db
from app.router.deps import get_current_user
from app.schemas.credential import (
    VALID_PROVIDER_TYPES,
    CredentialListResponse,
    CredentialResponse,
    SaveCredentialRequest,
)
from app.service.credential_service import CredentialService
from app.service.tenant_service import TenantService

router = APIRouter(prefix="/credentials", tags=["credentials"])


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
async def save_credential(
    body: SaveCredentialRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Validates and stores a BYOK credential -- the tenant's shared one
    if `tenant_id` is given, otherwise the caller's own personal one.

    Args:
        body: The provider to save a credential for, the raw API key, and
            an optional tenant_id selecting which credential this is.
        current_user: The authenticated caller.
        db: An active async SQLAlchemy session.

    Raises:
        HTTPException: 422 if provider_type is unknown, or if the key
            fails provider-side validation; 403 if tenant_id is given and
            the caller isn't that tenant's Owner; 404 if tenant_id doesn't
            exist.
    """
    if body.provider_type not in VALID_PROVIDER_TYPES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown provider_type"
        )
    if body.tenant_id is not None:
        owner = await TenantService.require_owner(
            db, user=current_user, tenant_id=body.tenant_id
        )
        await CredentialService.save_credential(
            db,
            tenant_id=body.tenant_id,
            provider_type=body.provider_type,
            api_key=body.api_key,
            set_by_user_id=owner.id,
        )
        return
    await CredentialService.save_credential(
        db,
        user_id=current_user.id,
        provider_type=body.provider_type,
        api_key=body.api_key,
    )


@router.get("", response_model=CredentialListResponse)
async def list_credentials(
    tenant_id: uuid.UUID | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CredentialListResponse:
    """Lists saved BYOK credentials (masked) -- the tenant's shared ones
    if `tenant_id` is given, otherwise the caller's own personal ones.
    Each item's `scope` reflects which kind it is.

    Args:
        tenant_id: If given, lists that tenant's credentials instead of
            the caller's personal ones (caller must be its Owner).
        current_user: The authenticated caller.
        db: An active async SQLAlchemy session.

    Returns:
        Every provider_type with a stored credential in the selected
        scope, each with a masked preview and when it was last validated.

    Raises:
        HTTPException: 403 if tenant_id is given and the caller isn't that
            tenant's Owner; 404 if tenant_id doesn't exist.
    """
    if tenant_id is not None:
        await TenantService.require_owner(db, user=current_user, tenant_id=tenant_id)
        credentials = await CredentialService.list_credentials(db, tenant_id=tenant_id)
        scope = "tenant"
    else:
        credentials = await CredentialService.list_credentials(
            db, user_id=current_user.id
        )
        scope = "personal"
    return CredentialListResponse(
        credentials=[
            CredentialResponse(
                provider_type=credential.provider_type,
                masked_preview=CredentialService.masked_preview(credential),
                validated_at=credential.validated_at,
                scope=scope,
            )
            for credential in credentials
        ]
    )


@router.delete("/{provider_type}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(
    provider_type: str,
    tenant_id: uuid.UUID | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Removes a BYOK credential -- the tenant's shared one if `tenant_id`
    is given, otherwise the caller's own personal one.

    Args:
        provider_type: Which stored credential to remove (e.g. "openai_llm").
        tenant_id: If given, removes that tenant's credential instead of
            the caller's personal one (caller must be its Owner).
        current_user: The authenticated caller.
        db: An active async SQLAlchemy session.

    Raises:
        HTTPException: 403 if tenant_id is given and the caller isn't that
            tenant's Owner; 404 if tenant_id doesn't exist.
    """
    if tenant_id is not None:
        await TenantService.require_owner(db, user=current_user, tenant_id=tenant_id)
        await CredentialService.delete_credential(
            db, tenant_id=tenant_id, provider_type=provider_type
        )
        return
    await CredentialService.delete_credential(
        db, user_id=current_user.id, provider_type=provider_type
    )
