import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

TenantRole = Literal["owner", "admin", "member"]


class TenantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    domain: str
    is_active: bool
    created_at: datetime


class MyTenantResponse(TenantResponse):
    """GET /tenants/me only -- the caller's own role is meaningful there
    (deciding whether to show admin-only member-management controls, and
    whether they're the Owner: `role == "owner"`) but isn't a property of
    the tenant itself, so it doesn't belong on the plain TenantResponse
    other endpoints use.
    """

    role: TenantRole


class TenantMemberResponse(BaseModel):
    """One roster entry. There's no separate membership row anymore, so
    `id` and `user_id` are both the member's user id (`id` kept so the
    response shape stays stable). Owner status is `role == "owner"`."""

    id: uuid.UUID
    user_id: uuid.UUID
    username: str
    email: str
    role: TenantRole
    has_company_access: bool
    created_at: datetime


class PaginatedMembersResponse(BaseModel):
    """GET /tenants/{id}/members -- a page of the roster plus enough to
    render pagination controls (search matches against username/email
    are applied before paging, so `total`/`total_pages` reflect the
    filtered count, not the tenant's full membership)."""

    items: list[TenantMemberResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class UpdateMemberRoleRequest(BaseModel):
    role: TenantRole


class KnowledgeAccessBulkRevokeResponse(BaseModel):
    revoked_count: int


class KnowledgeAccessBulkGrantResponse(BaseModel):
    granted_count: int


class MemberBulkRemoveResponse(BaseModel):
    removed_count: int
