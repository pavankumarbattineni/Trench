"""Business logic for provisioning and retrieving Trench's application users.

Firebase still owns identity/passwords, but a Trench user row is now
correlated to a Firebase account by email rather than Firebase UID (the
`users` table has no firebase_uid column) -- email is Trench's own
identity key, matching a typical "your email is your account" model.

Signup and signin are deliberately separate operations here (see
AuthService): signup explicitly creates the row via `create_user`, signin
only ever looks one up (`get_by_email`) and never creates one. There is
no lazy "create on first login" path -- an unregistered Firebase identity
must go through signup before it can ever sign in.
"""

import secrets
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User

_USERNAME_TAKEN = HTTPException(
    status.HTTP_409_CONFLICT, "That username is already taken"
)

ADMIN_ROLE = "admin"
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
        register_as_admin: bool = False,
    ) -> User:
        """Creates a new Trench user row for a just-verified Firebase account.

        Callers (AuthService.signup) must already have checked that no user
        exists for this email -- this always creates a new row, never
        returns an existing one.

        Args:
            db: An active async SQLAlchemy session.
            email: The user's email address, as claimed by Firebase.
            desired_username: A username collected at signup (the
                email/password form). When absent (e.g. "Continue with
                Google" on the signup page, which collects no username), one
                is generated from the email's local part instead.
            register_as_admin: Whether the signer-upper asked to become the
                Trench application's administrator. Only ever honored when
                no administrator exists yet (see `admin_exists`) -- this is
                the one and only way a user can ever end up with
                role="admin", and it's re-derived server-side, never taken
                from a raw client-supplied role value.

        Returns:
            The newly created User row.

        Raises:
            HTTPException: 409 if `desired_username` is already taken.
            RuntimeError: If no unique generated username could be allocated.
        """
        can_be_admin = register_as_admin and not await cls.admin_exists(db)
        role = ADMIN_ROLE if can_be_admin else USER_ROLE

        if desired_username is not None:
            return await cls._create_user(
                db, email=email, username=desired_username, role=role
            )

        for candidate_username in cls._generated_username_candidates(email):
            try:
                return await cls._create_user(
                    db, email=email, username=candidate_username, role=role
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

    @staticmethod
    async def admin_exists(db: AsyncSession) -> bool:
        """Whether a Trench application administrator has already been
        registered -- the bootstrap gate for `register_as_admin` at signup,
        and for whether the signup page should even offer that option."""
        result = await db.execute(
            select(func.count()).select_from(User).where(User.role == ADMIN_ROLE)
        )
        return (result.scalar_one() or 0) > 0
