"""BYOK credential management endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
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

router = APIRouter(prefix="/credentials", tags=["credentials"])


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
async def save_credential(
    body: SaveCredentialRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Validates and stores a BYOK credential for the authenticated user.

    Args:
        body: The provider to save a credential for, and the raw API key.

    Raises:
        HTTPException: 422 if provider_type is unknown, or if the key
            fails provider-side validation.
    """
    if body.provider_type not in VALID_PROVIDER_TYPES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown provider_type"
        )
    await CredentialService.save_credential(
        db,
        user_id=current_user.id,
        provider_type=body.provider_type,
        api_key=body.api_key,
    )


@router.get("", response_model=CredentialListResponse)
async def list_credentials(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CredentialListResponse:
    """Lists the authenticated user's saved BYOK credentials.

    Returns only a masked preview per credential -- never the full key.
    """
    credentials = await CredentialService.list_credentials(db, user_id=current_user.id)
    return CredentialListResponse(
        credentials=[
            CredentialResponse(
                provider_type=credential.provider_type,
                masked_preview=CredentialService.masked_preview(credential),
                validated_at=credential.validated_at,
            )
            for credential in credentials
        ]
    )


@router.delete("/{provider_type}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(
    provider_type: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Removes a BYOK credential for the authenticated user."""
    await CredentialService.delete_credential(
        db, user_id=current_user.id, provider_type=provider_type
    )
