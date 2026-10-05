import uuid
from datetime import datetime

from pydantic import BaseModel

from app.schemas.chat import KnowledgeType
from app.schemas.tenant import TenantRole


class UpdateModelRequest(BaseModel):
    model_id: uuid.UUID
    # Which scope this model is being picked for -- determines whether
    # selection is validated against the user's own BYOK credential or
    # their tenant's shared one (see UserPreferenceService.update_model).
    knowledge_type: KnowledgeType = "personal"


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
    # Lifetime count of personal documents ever uploaded (never
    # decremented by deletion -- see Document.chunk_count's own free-tier
    # comment) and the free-tier cap it's checked against
    # (DocumentService.FREE_DOCUMENT_LIMIT). Doesn't account for the
    # personal-Pinecone-BYOK exemption that lifts the cap server-side --
    # a BYOK user may still see these equal and be able to upload anyway.
    personal_documents_uploaded_count: int
    personal_document_limit: int
