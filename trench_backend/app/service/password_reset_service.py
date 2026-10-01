"""Fully custom password-reset flow: the backend generates its own
token, emails it via SMTP, and updates the password through the Firebase
Admin SDK -- Firebase remains the password *store*, but owns none of the
reset email or verification flow (replacing sendPasswordResetEmail/
oobCode/verifyPasswordResetCode on the frontend).
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import PasswordResetToken, User
from app.utils import firebase as firebase_utils
from app.utils.email import send_email

RESET_TOKEN_EXPIRY = timedelta(hours=1)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


class PasswordResetService:
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

        reset_url = f"https://app.trench.example/reset-password?token={raw_token}"
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
