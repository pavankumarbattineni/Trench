"""Grants/revokes/checks a user's permission to query company knowledge.

Belonging to an organization does not itself grant company-knowledge
access -- an admin must explicitly grant it. This is the authorization
check the retrieval layer relies on before a "company" query is ever
allowed to reach the vector store (see RetrievalService).
"""

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import KnowledgeAccess, OrganizationMember


class KnowledgeAccessService:
    @staticmethod
    async def grant(
        db: AsyncSession,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        knowledge_type: str = "company",
    ) -> KnowledgeAccess:
        result = await db.execute(
            select(KnowledgeAccess).where(
                KnowledgeAccess.organization_id == organization_id,
                KnowledgeAccess.user_id == user_id,
                KnowledgeAccess.knowledge_type == knowledge_type,
            )
        )
        access = result.scalar_one_or_none()
        if access is None:
            access = KnowledgeAccess(
                organization_id=organization_id,
                user_id=user_id,
                knowledge_type=knowledge_type,
            )
            db.add(access)
        else:
            access.is_active = True
        await db.commit()
        await db.refresh(access)
        return access

    @staticmethod
    async def revoke(
        db: AsyncSession,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        knowledge_type: str = "company",
    ) -> None:
        """Revokes a grant if one exists. Idempotent: revoking a user who
        was never granted access (or already revoked) is a no-op success,
        not an error -- this is the "desired state" side of a toggle, not
        a strict "delete this specific existing thing" operation."""
        result = await db.execute(
            select(KnowledgeAccess).where(
                KnowledgeAccess.organization_id == organization_id,
                KnowledgeAccess.user_id == user_id,
                KnowledgeAccess.knowledge_type == knowledge_type,
            )
        )
        access = result.scalar_one_or_none()
        if access is None:
            return
        await db.delete(access)
        await db.commit()

    @staticmethod
    async def revoke_all(
        db: AsyncSession, *, organization_id: uuid.UUID, knowledge_type: str = "company"
    ) -> int:
        """Revokes every member's company-knowledge grant for an
        organization in one shot. Never touches org admins' effective
        access (that's derived from OrganizationMember.role, not from
        these rows -- see `authorized_company_organization_id`), so this
        never needs to special-case the owner.

        Returns:
            How many grants were revoked.
        """
        result = await db.execute(
            delete(KnowledgeAccess)
            .where(
                KnowledgeAccess.organization_id == organization_id,
                KnowledgeAccess.knowledge_type == knowledge_type,
            )
            .returning(KnowledgeAccess.id)
        )
        revoked_ids = result.scalars().all()
        await db.commit()
        return len(revoked_ids)

    @staticmethod
    async def grant_all(
        db: AsyncSession, *, organization_id: uuid.UUID, knowledge_type: str = "company"
    ) -> int:
        """Grants company-knowledge access to every current member of the
        organization in one shot -- creating a new grant, or reactivating
        an existing inactive one, for whichever members aren't already
        actively granted. Org admins are unaffected either way (their
        access is derived from role, not a grant row), but granting them
        one too is harmless.

        Returns:
            How many members were newly granted access (already-active
            grants aren't re-counted).
        """
        member_ids_result = await db.execute(
            select(OrganizationMember.user_id).where(
                OrganizationMember.organization_id == organization_id
            )
        )
        member_ids = [row[0] for row in member_ids_result.all()]
        if not member_ids:
            return 0

        existing_result = await db.execute(
            select(KnowledgeAccess).where(
                KnowledgeAccess.organization_id == organization_id,
                KnowledgeAccess.knowledge_type == knowledge_type,
                KnowledgeAccess.user_id.in_(member_ids),
            )
        )
        existing_by_user = {
            access.user_id: access for access in existing_result.scalars().all()
        }

        granted = 0
        for user_id in member_ids:
            existing = existing_by_user.get(user_id)
            if existing is None:
                db.add(
                    KnowledgeAccess(
                        organization_id=organization_id,
                        user_id=user_id,
                        knowledge_type=knowledge_type,
                    )
                )
                granted += 1
            elif not existing.is_active:
                existing.is_active = True
                granted += 1
        await db.commit()
        return granted

    @staticmethod
    async def list_for_organization(
        db: AsyncSession, organization_id: uuid.UUID
    ) -> list[KnowledgeAccess]:
        result = await db.execute(
            select(KnowledgeAccess).where(
                KnowledgeAccess.organization_id == organization_id,
                KnowledgeAccess.is_active.is_(True),
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def has_company_access(
        db: AsyncSession, *, user_id: uuid.UUID, organization_id: uuid.UUID
    ) -> bool:
        result = await db.execute(
            select(KnowledgeAccess.id).where(
                KnowledgeAccess.organization_id == organization_id,
                KnowledgeAccess.user_id == user_id,
                KnowledgeAccess.knowledge_type == "company",
                KnowledgeAccess.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def authorized_company_organization_id(
        db: AsyncSession, *, user_id: uuid.UUID
    ) -> uuid.UUID | None:
        """Returns the organization_id the user may query company knowledge
        for, or None if they have no such authorization.

        This is the single check every company-knowledge code path (chat
        retrieval, document listing) must go through -- an org admin is
        always authorized for their own org's knowledge (they manage it),
        everyone else needs an explicit KnowledgeAccess grant.
        """
        membership_result = await db.execute(
            select(OrganizationMember).where(OrganizationMember.user_id == user_id)
        )
        membership = membership_result.scalar_one_or_none()
        if membership is None:
            return None
        if membership.role == "admin":
            return membership.organization_id

        has_access = await KnowledgeAccessService.has_company_access(
            db, user_id=user_id, organization_id=membership.organization_id
        )
        return membership.organization_id if has_access else None

    @staticmethod
    async def user_has_any_company_access(
        db: AsyncSession, *, user_id: uuid.UUID
    ) -> bool:
        """Whether /users/me should advertise the "Company" knowledge option."""
        organization_id = (
            await KnowledgeAccessService.authorized_company_organization_id(
                db, user_id=user_id
            )
        )
        return organization_id is not None
