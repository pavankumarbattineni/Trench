"""Business logic for the Firebase-backed authentication flow.

Firebase owns passwords and identity; this service verifies Firebase ID
tokens, provisions the matching Postgres user, and issues/verifies Trench's
own short-lived access token and longer-lived refresh token (both stateless
JWTs -- see app.utils.security). There is no server-side session store:
a refresh token stays valid until it expires or the frontend discards it.
"""

from datetime import UTC, datetime

from fastapi import HTTPException, status
from firebase_admin import auth as firebase_auth
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.service.user_service import UserService
from app.utils import firebase as firebase_utils
from app.utils.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
)

_UNAUTHENTICATED = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
_INVALID_FIREBASE_TOKEN = HTTPException(
    status.HTTP_401_UNAUTHORIZED, "Invalid or expired Firebase token"
)
_RECENT_LOGIN_REQUIRED = HTTPException(
    status.HTTP_401_UNAUTHORIZED,
    "Please sign in again before deleting your account",
)
_NOT_REGISTERED = HTTPException(
    status.HTTP_404_NOT_FOUND,
    "No account found for this email. Please sign up first.",
)
_ALREADY_REGISTERED = HTTPException(
    status.HTTP_409_CONFLICT,
    "An account already exists for this email. Please sign in instead.",
)

# How fresh a Firebase ID token's issued-at time must be to count as "recent
# authentication" for a destructive operation like account deletion.
_RECENT_LOGIN_WINDOW_SECONDS = 300


class AuthService:
    """Verifies Firebase identity and issues/consumes Trench's own JWTs."""

    @staticmethod
    def verify_firebase_token(id_token: str) -> dict:
        """Verifies a Firebase ID token and returns its decoded claims.

        Args:
            id_token: The Firebase ID token issued to the client after sign-in.

        Returns:
            The decoded token claims (includes "uid", "email", "iat").

        Raises:
            HTTPException: 401 if the token is missing, malformed, expired,
                or revoked.
        """
        try:
            return firebase_utils.verify_firebase_id_token(id_token)
        except (
            firebase_auth.InvalidIdTokenError,
            firebase_auth.ExpiredIdTokenError,
            firebase_auth.RevokedIdTokenError,
            firebase_auth.CertificateFetchError,
            ValueError,
        ) as exc:
            raise _INVALID_FIREBASE_TOKEN from exc

    @classmethod
    async def signup(
        cls,
        db: AsyncSession,
        *,
        id_token: str,
        username: str | None,
        register_as_admin: bool,
    ) -> User:
        """Registers a new Trench user from a verified Firebase ID token.

        Deliberately does NOT return session tokens -- signup and signin are
        separate actions (see `create_session`); the frontend sends the user
        to the sign-in page after this succeeds rather than logging them in
        immediately.

        Args:
            db: An active async SQLAlchemy session.
            id_token: The Firebase ID token from the client's just-completed
                signup (email/password creation, or a first-time Google
                sign-in used as a signup action).
            username: A username collected at signup (absent for Google,
                which auto-generates one from the email instead).
            register_as_admin: Whether the signer-upper asked to become the
                Trench application's administrator (only ever honored if
                none exists yet -- see `UserService.create_user`).

        Returns:
            The newly created User row.

        Raises:
            HTTPException: 409 if an account already exists for this email,
                or if `username` is already taken.
        """
        claims = cls.verify_firebase_token(id_token)
        if await UserService.get_by_email(db, claims["email"]) is not None:
            raise _ALREADY_REGISTERED
        return await UserService.create_user(
            db,
            email=claims["email"],
            desired_username=username,
            register_as_admin=register_as_admin,
        )

    @classmethod
    async def create_session(
        cls, db: AsyncSession, id_token: str
    ) -> tuple[User, str, str]:
        """Starts a Trench session from a verified Firebase ID token.

        Only ever resolves an *existing* user -- never creates one. An
        unregistered Firebase identity (valid token, but no matching Trench
        user) must go through `signup` first.

        Args:
            db: An active async SQLAlchemy session.
            id_token: The Firebase ID token from the client's sign-in.

        Returns:
            A (user, access_token, refresh_token) tuple.

        Raises:
            HTTPException: 404 if no Trench account exists for this email.
        """
        claims = cls.verify_firebase_token(id_token)
        user = await UserService.get_by_email(db, claims["email"])
        if user is None:
            raise _NOT_REGISTERED
        return user, create_access_token(user.id), create_refresh_token(user.id)

    @staticmethod
    async def resolve_access_token(db: AsyncSession, token: str | None) -> User:
        """Resolves the current user from an access-token cookie.

        Args:
            db: An active async SQLAlchemy session.
            token: The raw access_token cookie value, if present.

        Returns:
            The authenticated, active User.

        Raises:
            HTTPException: 401 if the token is missing, invalid, or the user
                is missing/inactive.
        """
        if not token:
            raise _UNAUTHENTICATED
        try:
            user_id = decode_access_token(token)
        except TokenError as exc:
            raise _UNAUTHENTICATED from exc

        user = await UserService.get_by_id(db, user_id)
        if user is None or not user.is_active:
            raise _UNAUTHENTICATED
        return user

    @staticmethod
    async def refresh_session(
        db: AsyncSession, token: str | None
    ) -> tuple[User, str, str]:
        """Rotates a refresh-token cookie into a fresh access/refresh pair.

        Args:
            db: An active async SQLAlchemy session.
            token: The raw refresh_token cookie value, if present.

        Returns:
            A (user, access_token, refresh_token) tuple.

        Raises:
            HTTPException: 401 if the token is missing, invalid, or the user
                is missing/inactive.
        """
        if not token:
            raise _UNAUTHENTICATED
        try:
            user_id = decode_refresh_token(token)
        except TokenError as exc:
            raise _UNAUTHENTICATED from exc

        user = await UserService.get_by_id(db, user_id)
        if user is None or not user.is_active:
            raise _UNAUTHENTICATED

        return user, create_access_token(user.id), create_refresh_token(user.id)

    @classmethod
    async def delete_account(
        cls, db: AsyncSession, current_user: User, id_token: str
    ) -> None:
        """Permanently deletes a user's Postgres row and Firebase identity.

        Requires a freshly issued Firebase ID token (see
        `_RECENT_LOGIN_WINDOW_SECONDS`) as proof of recent authentication,
        since this is a destructive, irreversible operation.

        Postgres data is deleted before the Firebase account, so a failure
        never leaves orphaned data with no account left to reclaim it --
        worst case, the Firebase account survives with nothing behind it,
        which is retriable.

        Args:
            db: An active async SQLAlchemy session.
            current_user: The authenticated user requesting deletion.
            id_token: A freshly issued Firebase ID token proving recent auth.

        Raises:
            HTTPException: 403 if the token belongs to a different account
                (matched by email -- Trench has no stored Firebase UID),
                401 if the token is stale, 500 if Firebase deletion fails
                after Postgres data was already removed.
        """
        claims = cls.verify_firebase_token(id_token)

        if claims["email"] != current_user.email:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Token does not match this account"
            )

        issued_at = datetime.fromtimestamp(claims["iat"], tz=UTC)
        age_seconds = (datetime.now(UTC) - issued_at).total_seconds()
        if age_seconds > _RECENT_LOGIN_WINDOW_SECONDS:
            raise _RECENT_LOGIN_REQUIRED

        firebase_uid = claims["uid"]
        await db.delete(current_user)
        await db.commit()

        try:
            firebase_utils.delete_firebase_user(firebase_uid)
        except Exception as exc:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Account data was deleted, but removing the Firebase identity "
                "failed. Please contact support.",
            ) from exc
