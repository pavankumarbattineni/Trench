"""Shared FastAPI dependencies used across routers."""

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.database.session import get_db
from app.service.auth_service import AuthService
from app.utils.cookies import ACCESS_TOKEN_COOKIE


async def get_current_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> User:
    """Resolves the authenticated user from the access_token cookie.

    Args:
        request: The incoming request (read for its cookies).
        db: An active async SQLAlchemy session.

    Returns:
        The authenticated, active User.

    Raises:
        HTTPException: 401 if there is no valid, current session.
    """
    token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    return await AuthService.resolve_access_token(db, token)
