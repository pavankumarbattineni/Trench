"""Business logic for provisioning and retrieving Trench's application users.

Firebase still owns identity/passwords, but a Trench user row is now
correlated to a Firebase account by email rather than Firebase UID (the
`users` table has no firebase_uid column) -- email is Trench's own
identity key, matching a typical "your email is your account" model.
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


class UserService:
    """Handles creation and lookup of Trench's application-level user records."""

    @staticmethod
    def _generated_username_candidates(email: str) -> list[str]:
        base = email.split("@")[0]
        return [base, f"{base}-{secrets.token_hex(3)}"]

    @classmethod
    async def get_or_create_user(
        cls,
        db: AsyncSession,
        *,
        email: str,
        desired_username: str | None = None,
    ) -> User:
        """Looks up a user by email, creating one on first sign-in (lazy upsert).

        Args:
            db: An active async SQLAlchemy session.
            email: The user's email address, as claimed by Firebase.
            desired_username: A username the frontend collected at signup (e.g. the
                email/password signup form). Only used the first time a user is
                created; ignored for an existing user or when absent (e.g. Google
                sign-in, which doesn't collect one), in which case a username is
                generated from the email instead.

        Returns:
            The existing or newly created User row.

        Raises:
            HTTPException: 409 if `desired_username` is already taken.
            RuntimeError: If no unique generated username could be allocated.
        """
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user is not None:
            return user

        if desired_username is not None:
            return await cls._create_user(db, email=email, username=desired_username)

        for candidate_username in cls._generated_username_candidates(email):
            try:
                return await cls._create_user(
                    db, email=email, username=candidate_username
                )
            except HTTPException:
                continue

        raise RuntimeError(f"Could not allocate a unique username for {email}")

    @staticmethod
    async def _create_user(db: AsyncSession, *, email: str, username: str) -> User:
        user = User(email=email, username=username)
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
