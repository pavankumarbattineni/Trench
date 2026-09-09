"""Authentication endpoints: session exchange, refresh, logout, account deletion."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.database.session import get_db
from app.router.deps import get_current_user
from app.schemas.auth import AccountDeletionRequest, FirebaseSessionRequest
from app.schemas.user import UserResponse
from app.service.auth_service import AuthService
from app.utils.cookies import (
    REFRESH_TOKEN_COOKIE,
    clear_auth_cookies,
    set_auth_cookies,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/session", response_model=UserResponse)
async def create_session(
    body: FirebaseSessionRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Exchanges a verified Firebase ID token for a Trench session.

    Verifies the Firebase ID token, lazily provisions the matching Postgres
    user on first sign-in, and sets httpOnly access/refresh token cookies.

    Args:
        body: The Firebase ID token obtained by the frontend after sign-in,
            plus an optional username (used only on first sign-in).
        response: Used to set the resulting auth cookies.
        db: An active async SQLAlchemy session.

    Returns:
        The authenticated user's profile.
    """
    user, access_token, refresh_token = await AuthService.create_session(
        db, body.id_token, body.username
    )
    set_auth_cookies(response, access_token, refresh_token)
    return user


@router.post("/refresh", response_model=UserResponse)
async def refresh_session(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Rotates the refresh_token cookie into a fresh access/refresh pair.

    Args:
        request: Read for its refresh_token cookie.
        response: Used to set the rotated auth cookies.
        db: An active async SQLAlchemy session.

    Returns:
        The authenticated user's profile.
    """
    token = request.cookies.get(REFRESH_TOKEN_COOKIE)
    try:
        user, access_token, refresh_token = await AuthService.refresh_session(db, token)
    except HTTPException:
        clear_auth_cookies(response)
        raise
    set_auth_cookies(response, access_token, refresh_token)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    """Clears Trench's auth cookies for this browser.

    There is no server-side session to invalidate (tokens are stateless
    JWTs); the frontend also signs out of Firebase separately.

    Args:
        response: Used to clear the auth cookies.
    """
    clear_auth_cookies(response)


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    body: AccountDeletionRequest,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Permanently deletes the authenticated user's account.

    Requires a freshly issued Firebase ID token as proof of recent
    authentication, in addition to a valid Trench session.

    Args:
        body: A freshly issued Firebase ID token for the same account.
        response: Used to clear the auth cookies once deletion succeeds.
        current_user: The authenticated user requesting deletion.
        db: An active async SQLAlchemy session.
    """
    await AuthService.delete_account(db, current_user, body.id_token)
    clear_auth_cookies(response)
