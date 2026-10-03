import uuid
from datetime import datetime

from pydantic import BaseModel

from app.schemas.tenant import TenantRole


class UpdateModelRequest(BaseModel):
    model_id: uuid.UUID


class SelectedTenantResponse(BaseModel):
    id: uuid.UUID
    name: str
    # Mirrors User.role -- the user's one and only role (there's no
    # separate Trench-wide application role anymore).
    role: TenantRole


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    username: str
    is_active: bool
    created_at: datetime
    model_id: uuid.UUID | None
    model_name: str | None
    tenant: SelectedTenantResponse | None
    has_company_access: bool
