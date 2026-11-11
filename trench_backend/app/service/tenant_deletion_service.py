"""Permanently deletes a tenant (Owner only).

What happens to each kind of data:

- **Members (the Owner included)** are *detached*, never deleted -- exactly
  what removing a single member does (TenantService.remove_member): their
  `tenant_id` is cleared, their role resets to "member" and any
  company-knowledge grant is revoked. Their accounts, personal documents and
  chats are untouched.
- **Company documents**: database rows, stored files (Backblaze/local) and
  the tenant's `company:<id>` vector namespace are all removed.
- **Invitations and the tenant's shared provider credential** are removed by
  the database itself (`ON DELETE CASCADE` on their `tenant_id`).

Database work happens first, in one transaction, so the tenant is either
fully gone or untouched. File and vector cleanup runs after the commit and
is best-effort: a failure there is logged (orphaned bytes/vectors are
harmless and can be cleaned up later) and never resurrects the tenant.
"""

import logging
import uuid

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Document, Tenant, User
from app.service.document_storage_service import get_storage_provider
from app.service.embedding_service import EmbeddingService
from app.service.tenant_service import MEMBER, TenantService
from app.service.vector_store_service import company_namespace, get_vector_store

logger = logging.getLogger(__name__)

_ACTIVE_DOCUMENT_STATUSES = ("pending", "processing")

_NOT_OWNER = HTTPException(
    status.HTTP_403_FORBIDDEN, "Only the tenant owner can delete the tenant"
)
_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
_NAME_MISMATCH = HTTPException(
    status.HTTP_422_UNPROCESSABLE_CONTENT,
    "Type the tenant's name exactly to confirm deletion.",
)
_DOCUMENTS_PROCESSING = HTTPException(
    status.HTTP_409_CONFLICT,
    "Some company documents are still processing. Wait for them to finish, "
    "then try again.",
)


class TenantDeletionService:
    @staticmethod
    async def delete_tenant(
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        acting_user: User,
        confirm_name: str,
    ) -> dict[str, int]:
        """Deletes the tenant and everything that belongs only to it.

        Args:
            db: An active async SQLAlchemy session.
            tenant_id: The tenant to delete.
            acting_user: The caller -- must be this tenant's Owner.
            confirm_name: The tenant's name, typed by the Owner as a
                deliberate confirmation (trimmed; case-sensitive).

        Returns:
            Counts for the response: members detached (Owner included) and
            company documents deleted.

        Raises:
            HTTPException: 403 if the caller isn't this tenant's Owner; 404
                if the tenant doesn't exist; 422 if `confirm_name` doesn't
                match; 409 if a company document is still being ingested
                (its vectors would land in the namespace being deleted).
        """
        if not TenantService.is_owner_of(acting_user, tenant_id):
            raise _NOT_OWNER
        tenant = await TenantService.get_by_id(db, tenant_id)
        if tenant is None:
            raise _NOT_FOUND
        if confirm_name.strip() != tenant.name:
            raise _NAME_MISMATCH

        processing = await db.execute(
            select(Document.id)
            .where(
                Document.tenant_id == tenant_id,
                Document.status.in_(_ACTIVE_DOCUMENT_STATUSES),
            )
            .limit(1)
        )
        if processing.first() is not None:
            raise _DOCUMENTS_PROCESSING

        storage_paths = list(
            (
                await db.execute(
                    select(Document.storage_path).where(Document.tenant_id == tenant_id)
                )
            ).scalars()
        )
        member_count = (
            await db.execute(
                select(func.count())
                .select_from(User)
                .where(User.tenant_id == tenant_id)
            )
        ).scalar_one()

        # Detach every member (Owner included) -- accounts survive.
        await db.execute(
            update(User)
            .where(User.tenant_id == tenant_id)
            .values(tenant_id=None, role=MEMBER, has_company_knowledge_access=False)
        )
        # Documents, invitations and the shared credential cascade with it.
        await db.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await db.commit()
        logger.info(
            "Tenant deleted | tenant_id=%s members_detached=%s documents=%s",
            tenant_id,
            member_count,
            len(storage_paths),
        )

        await TenantDeletionService._cleanup_external(tenant_id, storage_paths)
        return {
            "members_removed": member_count,
            "documents_deleted": len(storage_paths),
        }

    @staticmethod
    async def _cleanup_external(tenant_id: uuid.UUID, storage_paths: list[str]) -> None:
        storage = get_storage_provider()
        for path in storage_paths:
            try:
                await storage.delete(path)
            except Exception:
                logger.warning(
                    "Could not delete a deleted tenant's document file | "
                    "tenant_id=%s path=%s",
                    tenant_id,
                    path,
                )
        try:
            vector_store = get_vector_store(dimensions=EmbeddingService.DIMENSIONS)
            await vector_store.delete_namespace(namespace=company_namespace(tenant_id))
        except Exception:
            logger.warning(
                "Could not delete a deleted tenant's vector namespace | tenant_id=%s",
                tenant_id,
            )
