"""Builds the enriched user-profile response (GET /users/me and every auth
endpoint that returns a profile) -- the user's core fields plus their
selected LLM and tenant/company-access status.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import ProviderModel, User
from app.schemas.user import SelectedTenantResponse, UserResponse
from app.service.document_service import DocumentService
from app.service.knowledge_access_service import KnowledgeAccessService
from app.service.tenant_service import TenantService
from app.service.user_preference_service import UserPreferenceService


class UserProfileService:
    @staticmethod
    async def build_response(db: AsyncSession, user: User) -> UserResponse:
        user = await UserPreferenceService.ensure_defaults(db, user)
        model = await db.get(ProviderModel, user.model_id)

        tenant = (
            await TenantService.get_by_id(db, user.tenant_id)
            if user.tenant_id is not None
            else None
        )
        selected_tenant = (
            SelectedTenantResponse(id=tenant.id, name=tenant.name, role=user.role)
            if tenant is not None
            else None
        )

        return UserResponse(
            id=user.id,
            email=user.email,
            username=user.username,
            is_active=user.is_active,
            created_at=user.created_at,
            model_id=model.id if model else None,
            model_name=model.display_name if model else None,
            tenant=selected_tenant,
            has_company_access=selected_tenant is not None
            and KnowledgeAccessService.has_company_access(user),
            personal_documents_uploaded_count=user.documents_uploaded_count,
            personal_document_limit=DocumentService.FREE_DOCUMENT_LIMIT,
        )
