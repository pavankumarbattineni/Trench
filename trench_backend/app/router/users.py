"""Endpoints for the authenticated user's own profile."""

from fastapi import APIRouter, Depends

from app.database.models import User
from app.router.deps import get_current_user
from app.schemas.user import UserResponse

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(
    current_user: User = Depends(get_current_user),
) -> User:
    """Returns the authenticated user's profile.

    Args:
        current_user: Resolved from the access_token cookie.

    Returns:
        The authenticated user's profile.
    """
    return current_user
