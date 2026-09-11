import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

OrganizationRole = Literal["admin", "member"]


class OrganizationCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class OrganizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    domain: str
    owner_user_id: uuid.UUID
    is_active: bool
    created_at: datetime


class OrganizationCreatedResponse(OrganizationResponse):
    """POST /organizations only -- how many existing users on the same
    email domain were automatically added as members."""

    auto_added_members: int


class MyOrganizationResponse(OrganizationResponse):
    """GET /organizations/me only -- the caller's own role is meaningful
    there (deciding whether to show admin-only member-management
    controls) but isn't a property of the organization itself, so it
    doesn't belong on the plain OrganizationResponse other endpoints use.
    """

    my_role: OrganizationRole


class OrganizationMemberResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    username: str
    email: str
    role: OrganizationRole
    has_company_access: bool
    is_owner: bool
    created_at: datetime


class AddMemberRequest(BaseModel):
    username: str
    role: OrganizationRole = "member"


class UpdateMemberRoleRequest(BaseModel):
    role: OrganizationRole


class KnowledgeAccessResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    username: str
    knowledge_type: str
    is_active: bool
    created_at: datetime
