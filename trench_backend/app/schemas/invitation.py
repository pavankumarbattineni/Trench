import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

InvitationRole = Literal["admin", "member"]


class InvitationResponse(BaseModel):
    id: uuid.UUID
    email: str
    role: InvitationRole
    status: Literal["pending", "accepted", "revoked", "expired"]
    expires_at: datetime
    created_at: datetime
    accepted_at: datetime | None = None


class AcceptInvitationRequest(BaseModel):
    token: str
    id_token: str
    username: str | None = None


class BulkInvitationRowError(BaseModel):
    row: int
    email: str
    reason: str


class BulkInvitationResult(BaseModel):
    succeeded: list[InvitationResponse]
    failed: list[BulkInvitationRowError]
