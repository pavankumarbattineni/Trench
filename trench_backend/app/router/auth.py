"""Authentication endpoints: session exchange, refresh, logout, account
deletion.

Bearer-token auth, mirroring abyss_backend/abyss_frontend exactly: no
cookies, no server-side session -- the frontend receives an access/refresh
token pair in the response body, stores them itself, and attaches
`Authorization: Bearer <access_token>` on every subsequent request. There's
nothing for the backend to invalidate on "logout" (see the /logout
endpoint's own docstring) -- the real work still happens client-side
(clear the stored tokens, sign out of Firebase).
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.database.session import get_db
from app.router.deps import get_current_user
from app.schemas.auth import (
    AccountDeletionRequest,
    FirebaseSessionRequest,
    OwnerSignupOrganization,
    OwnerSignupRequest,
    OwnerSignupResponse,
    RefreshRequest,
    TokenResponse,
)
from app.schemas.invitation import AcceptInvitationRequest
from app.schemas.password_reset import (
    ConfirmPasswordResetRequest,
    RequestPasswordResetRequest,
)
from app.service.auth_service import AuthService
from app.service.invitation_service import InvitationService
from app.service.password_reset_service import PasswordResetService
from app.utils.security import create_access_token, create_refresh_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup/owner", response_model=OwnerSignupResponse)
async def signup_owner(
    body: OwnerSignupRequest,
    db: AsyncSession = Depends(get_db),
) -> OwnerSignupResponse:
    """Registers a new Trench user as the Owner of a brand-new
    organization. No session is issued here -- the Owner signs in
    separately via POST /auth/login afterward, same as every other
    account-creation path except invite-accept.

    Args:
        body: The Firebase ID token from signup, an optional username,
            and the new organization's display name (its domain is
            derived from the verified email, never user-entered).
        db: An active async SQLAlchemy session.

    Returns:
        The new organization's id/name/domain.

    Raises:
        HTTPException: 401 if the Firebase ID token is invalid; 409 if an
            account already exists for this email, the organization name
            is taken, or an organization already exists for this email's
            domain; 422 if the email is a public/personal provider
            domain (Gmail, Yahoo, etc.) rather than a work domain.
    """
    _user, organization = await AuthService.signup_owner(
        db,
        id_token=body.id_token,
        username=body.username,
        organization_name=body.organization_name,
    )
    return OwnerSignupResponse(
        organization=OwnerSignupOrganization(
            id=organization.id, name=organization.name, domain=organization.domain
        ),
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    body: FirebaseSessionRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Exchanges a verified Firebase ID token for a Trench access/refresh
    token pair.

    Verifies the Firebase ID token and resolves the matching Postgres user
    -- signin only, never creates one (see /auth/signup/owner or
    /auth/invitations/accept for that). The frontend calls GET /users/me
    afterward to fetch the profile -- this endpoint returns tokens only.

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


@router.post("/logout", status_code=204)
async def logout(current_user: User = Depends(get_current_user)) -> None:
    """Logs out the current user.

    Trench's JWTs are stateless and there is no server-side session store
    (see this module's docstring), so there is nothing here to actually
    revoke -- this endpoint exists for API completeness and so the
    frontend has one authoritative "goodbye" call to make, alongside
    clearing its own locally stored tokens and signing out of Firebase.
    Requiring authentication (rather than accepting any request) is the
    only real behavior: it rejects a request with no/invalid token before
    the client bothers clearing its own state.

    Args:
        current_user: The authenticated caller, resolved from the Bearer
            token -- unused beyond proving the token is currently valid.

    Raises:
        HTTPException: 401 if the Bearer token is missing or invalid.
    """


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


@router.post("/invitations/accept", response_model=TokenResponse)
async def accept_invitation(
    body: AcceptInvitationRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Accepts an organization invitation: verifies the Firebase ID token,
    validates the invite token, creates (or reuses) the Trench user,
    membership, and default knowledge-access grant, then issues a
    session -- accept and first sign-in are one action here, unlike the
    separate signup/login split everywhere else (there's no meaningful
    "accepted but not yet signed in" state for an invite).

    Args:
        body: The raw invitation token, a verified Firebase ID token, and
            an optional username (used only if this is the invitee's
            first-ever Trench signup).
        db: An active async SQLAlchemy session.

    Returns:
        A fresh access_token/refresh_token pair.

    Raises:
        HTTPException: 401 if the Firebase ID token is invalid; 404 if
            the invitation token doesn't exist or is no longer pending;
            403 if the authenticated email doesn't match the invited one.
    """
    claims = AuthService.verify_firebase_token(body.id_token)
    try:
        _invitation, user, _created = await InvitationService.accept(
            db,
            raw_token=body.token,
            authenticated_email=claims["email"],
            desired_username=body.username,
        )
    except InvitationService.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except InvitationService.EmailMismatchError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc

    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/password-reset/request", status_code=200)
async def request_password_reset(
    body: RequestPasswordResetRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """Requests a password-reset email. Always returns 200 regardless of
    whether the email matches an account, to avoid confirming/denying
    account existence."""
    await PasswordResetService.request(db, email=body.email)
    return {"detail": "If that email is registered, a reset link has been sent."}


@router.post("/password-reset/confirm", status_code=200)
async def confirm_password_reset(
    body: ConfirmPasswordResetRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """Completes a password reset.

    Raises:
        HTTPException: 404 if the token is missing, already used, or expired.
    """
    try:
        await PasswordResetService.confirm(
            db, raw_token=body.token, new_password=body.new_password
        )
    except PasswordResetService.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return {"detail": "Password updated."}
