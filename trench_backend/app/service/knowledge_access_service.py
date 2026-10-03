"""Grants/revokes/checks a user's permission to query company knowledge.

Belonging to a tenant does not itself grant company-knowledge access --
an Admin/Owner must explicitly grant it, which just flips
`User.has_company_knowledge_access`. Admins and the Owner are always
authorized by role instead (they manage the knowledge), regardless of
that flag. This is the authorization check the retrieval layer relies on
before a "company" query is ever allowed to reach the vector store (see
RetrievalService).

Every grant/revoke is scoped to `tenant_id` as well as the user, so an
Admin of one tenant can never flip the flag of a user in another.
"""

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.service.tenant_service import ADMIN_ROLES


class KnowledgeAccessService:
    @staticmethod
    async def _set_access(
        db: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID, allowed: bool
    ) -> None:
        await db.execute(
            update(User)
            .where(User.id == user_id, User.tenant_id == tenant_id)
            .values(has_company_knowledge_access=allowed)
            .execution_options(synchronize_session="fetch")
        )
        await db.commit()

    @classmethod
    async def grant(
        cls, db: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        """Idempotent: granting an already-granted user is a no-op."""
        await cls._set_access(db, tenant_id=tenant_id, user_id=user_id, allowed=True)

    @classmethod
    async def revoke(
        cls, db: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        """Idempotent: revoking a user who was never granted access (or
        already revoked) is a no-op success, not an error -- this is the
        "desired state" side of a toggle, not a strict "delete this
        specific existing thing" operation."""
        await cls._set_access(db, tenant_id=tenant_id, user_id=user_id, allowed=False)

    @staticmethod
    async def _set_access_for_all(
        db: AsyncSession, *, tenant_id: uuid.UUID, allowed: bool
    ) -> int:
        result = await db.execute(
            update(User)
            .where(
                User.tenant_id == tenant_id,
                User.has_company_knowledge_access.is_(not allowed),
            )
            .values(has_company_knowledge_access=allowed)
            .returning(User.id)
            .execution_options(synchronize_session="fetch")
        )
        changed_ids = result.scalars().all()
        await db.commit()
        return len(changed_ids)

    @classmethod
    async def revoke_all(cls, db: AsyncSession, *, tenant_id: uuid.UUID) -> int:
        """Revokes every member's company-knowledge grant for a tenant in
        one shot. Never touches admins' effective access (that's derived
        from `User.role`, not this flag -- see `has_company_access`), so
        this never needs to special-case the owner.

        Returns:
            How many grants were revoked (members that actually had one).
        """
        return await cls._set_access_for_all(db, tenant_id=tenant_id, allowed=False)

    @classmethod
    async def grant_all(cls, db: AsyncSession, *, tenant_id: uuid.UUID) -> int:
        """Grants company-knowledge access to every current member of the
        tenant in one shot. Admins are unaffected either way (their access
        is derived from role, not the flag), but flagging them too is
        harmless.

        Returns:
            How many members were newly granted access (already-granted
            members aren't re-counted).
        """
        return await cls._set_access_for_all(db, tenant_id=tenant_id, allowed=True)

    @staticmethod
    def has_company_access(user: User) -> bool:
        """Whether `user` may query their own tenant's company knowledge:
        always for an Admin/Owner, otherwise only with an explicit grant.
        Says nothing about *which* tenant -- see
        `authorized_company_tenant_id` for that."""
        if user.tenant_id is None:
            return False
        return user.role in ADMIN_ROLES or user.has_company_knowledge_access

    @classmethod
    async def authorized_company_tenant_id(
        cls, db: AsyncSession, *, user_id: uuid.UUID
    ) -> uuid.UUID | None:
        """Returns the tenant_id the user may query company knowledge for,
        or None if they have no such authorization.

        This is the single check every company-knowledge code path (chat
        retrieval, document listing) must go through -- always re-read
        from the database, never from a caller's claim.
        """
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None or not cls.has_company_access(user):
            return None
        return user.tenant_id

    @classmethod
    async def user_has_any_company_access(
        cls, db: AsyncSession, *, user_id: uuid.UUID
    ) -> bool:
        """Whether /users/me should advertise the "Company" knowledge option."""
        return await cls.authorized_company_tenant_id(db, user_id=user_id) is not None
