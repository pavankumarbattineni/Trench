"""Builds the enriched user-profile response (GET /users/me and every auth
endpoint that returns a profile) -- the user's core fields plus their
selected LLM and organization/company-access status.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import ProviderModel, User
from app.schemas.user import SelectedOrganizationResponse, UserResponse
from app.service.knowledge_access_service import KnowledgeAccessService
from app.service.organization_service import OrganizationService
from app.service.user_preference_service import UserPreferenceService


class UserProfileService:
    @staticmethod
    async def build_response(db: AsyncSession, user: User) -> UserResponse:
        user = await UserPreferenceService.ensure_defaults(db, user)
        model = await db.get(ProviderModel, user.model_id)

        membership = await OrganizationService.get_membership_for_user(db, user.id)
        organization = None
        has_company_access = False
        if membership is not None:
            org = await OrganizationService.get_by_id(db, membership.organization_id)
            organization = SelectedOrganizationResponse(
                id=org.id, name=org.name, role=membership.role
            )
            has_company_access = (
                membership.role == "admin"
                or await KnowledgeAccessService.has_company_access(
                    db, user_id=user.id, organization_id=membership.organization_id
                )
            )

        return UserResponse(
            id=user.id,
            email=user.email,
            username=user.username,
            is_active=user.is_active,
            created_at=user.created_at,
            model_id=model.id if model else None,
            model_name=model.display_name if model else None,
            organization=organization,
            has_company_access=has_company_access,
            role=user.role,
        )
