"""Grants/revokes/checks a user's permission to query company knowledge.

Belonging to an organization does not itself grant company-knowledge
access -- an admin must explicitly grant it. This is the authorization
check the retrieval layer relies on before a "company" query is ever
allowed to reach the vector store (see RetrievalService).
"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import KnowledgeAccess, OrganizationMember

_ACCESS_NOT_FOUND = HTTPException(
    status.HTTP_404_NOT_FOUND, "That user doesn't have company-knowledge access"
)


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
        result = await db.execute(
            select(KnowledgeAccess).where(
                KnowledgeAccess.organization_id == organization_id,
                KnowledgeAccess.user_id == user_id,
                KnowledgeAccess.knowledge_type == knowledge_type,
            )
        )
        access = result.scalar_one_or_none()
        if access is None:
            raise _ACCESS_NOT_FOUND
        await db.delete(access)
        await db.commit()

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
