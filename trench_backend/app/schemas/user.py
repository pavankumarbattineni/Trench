import uuid
from datetime import datetime

from pydantic import BaseModel


class UpdateModelRequest(BaseModel):
    model_id: uuid.UUID


class SelectedOrganizationResponse(BaseModel):
    id: uuid.UUID
    name: str
    role: str


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    username: str
    is_active: bool
    created_at: datetime
    model_id: uuid.UUID | None
    model_name: str | None
    organization: SelectedOrganizationResponse | None
    has_company_access: bool
    # The Trench *application* role ("admin" | "user") -- separate from
    # `organization.role`, an org's own admin/member role. See
    # app/database/models.py::User.role.
    role: str
