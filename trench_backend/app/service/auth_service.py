"""Business logic for the Firebase-backed authentication flow: identity
verification, Trench session issuance, and both password-change paths.

Firebase owns passwords and identity; this service verifies Firebase ID
tokens, provisions the matching Postgres user, and issues/verifies Trench's
own short-lived access token and longer-lived refresh token (both stateless
JWTs -- see app.utils.security). There is no server-side session store:
a refresh token stays valid until it expires or the frontend discards it.

Also holds the two password-change flows, each verifying "is this really
you" a different way before Firebase will accept a new password:
ChangePasswordService (authenticated Settings page, proves identity via
the current password) and PasswordResetService (signed-out Forgot
Password flow, proves identity via a custom emailed token).
"""

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from firebase_admin import auth as firebase_auth
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.models import PasswordResetToken, Tenant, User
from app.service.embedding_service import EmbeddingService
from app.service.tenant_service import TenantService
from app.service.user_service import UserService
from app.service.vector_store_service import get_vector_store, personal_namespace
from app.utils import firebase as firebase_utils
from app.utils.email import send_email
from app.utils.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
)

logger = logging.getLogger(__name__)

RESET_TOKEN_EXPIRY = timedelta(hours=1)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


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
_OWNER_CANNOT_DELETE_ACCOUNT = HTTPException(
    status.HTTP_409_CONFLICT,
    "You're the owner of a tenant -- transfer ownership or delete the "
    "tenant before deleting your account.",
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
    async def signup_owner(
        cls,
        db: AsyncSession,
        *,
        id_token: str,
        username: str | None,
        tenant_name: str,
    ) -> tuple[User, Tenant]:
        """Registers a new Trench user AND the tenant they own, as one
        action -- there is no more open signup; every account comes into
        being either this way (Owner, first user of a new company domain)
        or via InvitationService.accept (Member/Admin, invited by an
        existing Owner/Admin).

        Args:
            db: An active async SQLAlchemy session.
            id_token: The Firebase ID token from the client's just-completed
                signup (email/password creation, or a first-time Google
                sign-in used as a signup action).
            username: A username collected at signup (absent for Google,
                which auto-generates one from the email instead).
            tenant_name: The new tenant's display name. Its domain is
                derived from the verified email, never user-entered.

        Returns:
            (user, tenant) -- the user now has role="owner" and
            tenant_id set (by TenantService.create). No session is issued
            here; the new Owner signs in separately via POST /auth/login
            afterward.

        Raises:
            HTTPException: 409 if an account already exists for this
                email, or the tenant name/domain is already taken; 422 if
                the email is a public/personal provider domain.
        """
        claims = cls.verify_firebase_token(id_token)
        if await UserService.get_by_email(db, claims["email"]) is not None:
            raise _ALREADY_REGISTERED
        # Validated before the user row is created, not after -- a
        # domain-ineligible signup attempt must never leave behind an
        # orphaned User with no tenant.
        await TenantService.validate_domain_eligible_for_tenant(db, claims["email"])
        user = await UserService.create_user(
            db, email=claims["email"], desired_username=username
        )
        tenant = await TenantService.create(db, creator=user, name=tenant_name)
        await db.refresh(user)
        return user, tenant

    @staticmethod
    def issue_tokens(user_id: str) -> tuple[str, str]:
        """Mints a fresh (access_token, refresh_token) pair for a user.

        The single place all three Trench token-issuing paths (login,
        refresh, invitation-accept) go through, so a future change to how
        tokens are minted only has one call site to update.
        """
        return create_access_token(user_id), create_refresh_token(user_id)

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
        return user, *cls.issue_tokens(user.id)

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

        return user, *AuthService.issue_tokens(user.id)

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
                401 if the token is stale, 409 if the account is the
                permanent Owner of a tenant (transfer ownership or
                delete the tenant first), 500 if Firebase deletion
                fails after Postgres data was already removed.
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

        if await TenantService.get_owned_tenant(db, current_user) is not None:
            raise _OWNER_CANNOT_DELETE_ACCOUNT

        # Best-effort: a failed namespace wipe never blocks account
        # deletion (same rationale as the storage-delete failure handling
        # elsewhere -- one orphaned namespace is far smaller a problem
        # than an account the user can never delete). Logged so it can be
        # cleaned up by hand.
        try:
            vector_store = get_vector_store(dimensions=EmbeddingService.DIMENSIONS)
            await vector_store.delete_namespace(
                namespace=personal_namespace(current_user.id)
            )
        except Exception:
            logger.error(
                "Failed to delete personal Pinecone namespace on account "
                "deletion | user_id=%s",
                current_user.id,
            )

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


class ChangePasswordService:
    """Authenticated Settings > Change Password flow -- kept fully separate
    from PasswordResetService (the signed-out Forgot Password flow). Here
    the caller already has a valid session; the only thing left to prove
    is that they know the *current* password before Firebase will accept
    a new one.
    """

    class IncorrectCurrentPasswordError(Exception):
        """Raised when current_password doesn't match the account's
        password on file."""

    @classmethod
    async def change(
        cls, *, user: User, current_password: str, new_password: str
    ) -> None:
        """Raises:
        IncorrectCurrentPasswordError: current_password is wrong.
        """
        verified = await firebase_utils.verify_user_password(
            user.email, current_password
        )
        if not verified:
            raise cls.IncorrectCurrentPasswordError("Current password is incorrect")
        firebase_utils.set_user_password(user.email, new_password)


class PasswordResetService:
    """Fully custom password-reset flow: the backend generates its own
    token, emails it via SMTP, and updates the password through the
    Firebase Admin SDK -- Firebase remains the password *store*, but owns
    none of the reset email or verification flow (replacing
    sendPasswordResetEmail/oobCode/verifyPasswordResetCode on the
    frontend).
    """

    class InvalidTokenError(Exception):
        """Raised when a reset token is missing, expired, or already used."""

    @staticmethod
    async def request(db: AsyncSession, *, email: str) -> None:
        """Always succeeds from the caller's point of view, whether or not
        the email matches an account -- the router returns the same
        response either way to avoid confirming/denying account
        existence (the anti-enumeration UX the old Firebase-native flow
        already had)."""
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user is None:
            return

        raw_token = secrets.token_urlsafe(32)
        token = PasswordResetToken(
            user_id=user.id,
            token_hash=_hash_token(raw_token),
            expires_at=datetime.now(UTC) + RESET_TOKEN_EXPIRY,
        )
        db.add(token)
        await db.commit()

        frontend_base_url = get_settings().TRENCH_CONFIG.FRONTEND_BASE_URL
        reset_url = f"{frontend_base_url}/reset-password?token={raw_token}"
        await send_email(
            to=email,
            subject="Reset your Trench password",
            html_body=(
                f'<p><a href="{reset_url}">Reset your password</a></p>'
                "<p>This link expires in 1 hour. If you didn't request this, "
                "you can ignore this email.</p>"
            ),
            text_body=(
                f"Reset your password: {reset_url}\n\n"
                "This link expires in 1 hour. If you didn't request this, "
                "you can ignore this email."
            ),
        )

    @classmethod
    async def confirm(
        cls, db: AsyncSession, *, raw_token: str, new_password: str
    ) -> None:
        """Raises:
        InvalidTokenError: token not found, already used, or expired.
        """
        token_hash = _hash_token(raw_token)
        result = await db.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.token_hash == token_hash
            )
        )
        token = result.scalar_one_or_none()
        if token is None or token.used_at is not None:
            raise cls.InvalidTokenError("Reset token not found or already used")
        if token.expires_at < datetime.now(UTC):
            raise cls.InvalidTokenError("Reset token has expired")

        user = await db.get(User, token.user_id)
        firebase_utils.set_user_password(user.email, new_password)

        token.used_at = datetime.now(UTC)
        await db.commit()
