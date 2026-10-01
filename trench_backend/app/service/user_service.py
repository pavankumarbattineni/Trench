"""Business logic for provisioning and retrieving Trench's application users.

Firebase still owns identity/passwords, but a Trench user row is now
correlated to a Firebase account by email rather than Firebase UID (the
`users` table has no firebase_uid column) -- email is Trench's own
identity key, matching a typical "your email is your account" model.

Every user row now comes into being through exactly one of two paths:
AuthService.signup_owner (the first user of a new organization, as its
Owner) or InvitationService.accept (an invited Member/Admin). Both call
`create_user` below, which always creates a new row -- there is no lazy
"create on first login" path.
"""

import secrets
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User

_USERNAME_TAKEN = HTTPException(
    status.HTTP_409_CONFLICT, "That username is already taken"
)

USER_ROLE = "user"


class UserService:
    """Handles creation and lookup of Trench's application-level user records."""

    @staticmethod
    def _generated_username_candidates(email: str) -> list[str]:
        base = email.split("@")[0]
        return [base, f"{base}-{secrets.token_hex(3)}"]

    @classmethod
    async def create_user(
        cls,
        db: AsyncSession,
        *,
        email: str,
        desired_username: str | None = None,
    ) -> User:
        """Creates a new Trench user row for a just-verified Firebase account.

        Callers must already have checked that no user exists for this
        email -- this always creates a new row, never returns an existing
        one.

        Args:
            db: An active async SQLAlchemy session.
            email: The user's email address, as claimed by Firebase.
            desired_username: A username collected at signup/invite-accept.
                When absent (e.g. "Continue with Google", which collects no
                username), one is generated from the email's local part
                instead.

        Returns:
            The newly created User row.

        Raises:
            HTTPException: 409 if `desired_username` is already taken.
            RuntimeError: If no unique generated username could be allocated.
        """
        if desired_username is not None:
            return await cls._create_user(
                db, email=email, username=desired_username, role=USER_ROLE
            )

        for candidate_username in cls._generated_username_candidates(email):
            try:
                return await cls._create_user(
                    db, email=email, username=candidate_username, role=USER_ROLE
                )
            except HTTPException:
                continue

        raise RuntimeError(f"Could not allocate a unique username for {email}")

    @staticmethod
    async def _create_user(
        db: AsyncSession, *, email: str, username: str, role: str = USER_ROLE
    ) -> User:
        user = User(email=email, username=username, role=role)
        db.add(user)
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise _USERNAME_TAKEN from exc
        await db.refresh(user)
        return user

    @staticmethod
    async def get_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
        """Fetches a user by primary key.

        Args:
            db: An active async SQLAlchemy session.
            user_id: The user's UUID primary key.

        Returns:
            The User row, or None if not found.
        """
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_username(db: AsyncSession, username: str) -> User | None:
        result = await db.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_email(db: AsyncSession, email: str) -> User | None:
        result = await db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()
