"""Authenticated Settings > Change Password flow -- kept fully separate
from PasswordResetService (the signed-out Forgot Password flow). Here the
caller already has a valid session; the only thing left to prove is that
they know the *current* password before Firebase will accept a new one.
"""

from app.database.models import User
from app.utils import firebase as firebase_utils


class ChangePasswordService:
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
            raise cls.IncorrectCurrentPasswordError(
                "Current password is incorrect"
            )
        firebase_utils.set_user_password(user.email, new_password)
