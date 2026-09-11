"""Authentication endpoints: session exchange, refresh, account deletion.

Bearer-token auth, mirroring abyss_backend/abyss_frontend exactly: no
cookies, no server-side session -- the frontend receives an access/refresh
token pair in the response body, stores them itself, and attaches
`Authorization: Bearer <access_token>` on every subsequent request. There's
nothing for the backend to invalidate on "logout"; that's handled entirely
client-side (clear the stored tokens, sign out of Firebase), so there's no
corresponding endpoint here.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.database.session import get_db
from app.router.deps import get_current_user
from app.schemas.auth import (
    AccountDeletionRequest,
    AdminStatusResponse,
    FirebaseSessionRequest,
    RefreshRequest,
    SignupRequest,
    SignupResponse,
    TokenResponse,
)
from app.service.auth_service import AuthService
from app.service.user_service import UserService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/admin-status", response_model=AdminStatusResponse)
async def admin_status(db: AsyncSession = Depends(get_db)) -> AdminStatusResponse:
    """Whether a Trench administrator has already been registered.

    Public (no auth required) -- lets the signup page decide whether to
    offer the "register as administrator" option at all. This is a UX
    convenience only: the backend independently re-checks the same
    condition when signup actually happens, so hiding/showing this option
    here is never the security boundary.
    """
    return AdminStatusResponse(admin_exists=await UserService.admin_exists(db))


@router.post("/signup", response_model=SignupResponse)
async def signup(
    body: SignupRequest,
    db: AsyncSession = Depends(get_db),
) -> SignupResponse:
    """Registers a new Trench user from a verified Firebase ID token.

    Deliberately issues no session tokens -- signup and signin are separate
    actions; the frontend sends the user to the sign-in page next rather
    than logging them in immediately.

    Args:
        body: The Firebase ID token from the client's just-completed signup,
            plus an optional username and an admin-registration request.
        db: An active async SQLAlchemy session.

    Returns:
        The newly created user's profile (no tokens).

    Raises:
        HTTPException: 401 if the Firebase ID token is invalid; 409 if an
            account already exists for this email, or the requested
            username is taken.
    """
    user = await AuthService.signup(
        db,
        id_token=body.id_token,
        username=body.username,
        register_as_admin=body.register_as_admin,
    )
    return SignupResponse(
        id=user.id,
        email=user.email,
        username=user.username,
        role=user.role,
        created_at=user.created_at,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    body: FirebaseSessionRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Exchanges a verified Firebase ID token for a Trench access/refresh
    token pair.

    Verifies the Firebase ID token and resolves the matching Postgres user
    -- signin only, never creates one (see /auth/signup for that). The
    frontend calls GET /users/me afterward to fetch the profile -- this
    endpoint returns tokens only.

    Args:
        body: The Firebase ID token obtained by the frontend after sign-in.
        db: An active async SQLAlchemy session.

    Returns:
        A fresh access_token/refresh_token pair.

    Raises:
        HTTPException: 401 if the Firebase ID token is missing, invalid,
            expired, or revoked; 404 if no Trench account exists yet for
            this email (sign up first).
    """
    _user, access_token, refresh_token = await AuthService.create_session(
        db, body.id_token
    )
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_session(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Rotates a refresh_token into a fresh access/refresh pair.

    Args:
        body: The refresh_token the frontend currently holds.
        db: An active async SQLAlchemy session.

    Returns:
        A fresh access_token/refresh_token pair.

    Raises:
        HTTPException: 401 if the refresh_token is missing, invalid, or
            belongs to a missing/inactive user.
    """
    _user, access_token, refresh_token = await AuthService.refresh_session(
        db, body.refresh_token
    )
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.delete("/account", status_code=204)
async def delete_account(
    body: AccountDeletionRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Permanently deletes the authenticated user's account.

    Requires a freshly issued Firebase ID token as proof of recent
    authentication, in addition to a valid Trench access token.

    Args:
        body: A freshly issued Firebase ID token for the same account.
        current_user: The authenticated user requesting deletion.
        db: An active async SQLAlchemy session.

    Raises:
        HTTPException: 401 if not authenticated, or if the Firebase token
            isn't recent enough; 403 if the token belongs to a different
            account (matched by email); 500 if Postgres data was deleted
            but removing the Firebase identity itself failed.
    """
    await AuthService.delete_account(db, current_user, body.id_token)
