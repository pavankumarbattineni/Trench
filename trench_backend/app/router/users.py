"""Endpoints for the authenticated user's own profile."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.database.session import get_db
from app.router.deps import get_current_user
from app.schemas.user import UpdateModelRequest, UserResponse
from app.service.user_preference_service import UserPreferenceService
from app.service.user_profile_service import UserProfileService

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Returns the authenticated user's profile.

    Includes their selected LLM (resolving lazily to the platform default
    the first time it's read) and their organization membership/
    company-knowledge-access status, if any.

    Args:
        current_user: The authenticated user.
        db: An active async SQLAlchemy session.

    Returns:
        The user's core profile fields, selected model, and
        organization/company-access status.
    """
    return await UserProfileService.build_response(db, current_user)


@router.patch("/me", response_model=UserResponse)
async def update_current_user_model(
    body: UpdateModelRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Updates the authenticated user's selected chat model.

    Args:
        body: The catalog model id to select (see GET /config for the
            list of selectable models).
        current_user: The authenticated user.
        db: An active async SQLAlchemy session.

    Returns:
        The user's updated profile.

    Raises:
        HTTPException: 404 if the model id doesn't exist; 422 if it exists
            but is no longer active/selectable.
    """
    user = await UserPreferenceService.update_model(db, current_user, body.model_id)
    return await UserProfileService.build_response(db, user)
