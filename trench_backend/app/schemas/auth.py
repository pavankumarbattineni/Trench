import uuid
from typing import Annotated

from pydantic import BaseModel, Field

Username = Annotated[
    str, Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_-]+$")
]


class FirebaseSessionRequest(BaseModel):
    """POST /auth/login (signin) only -- no username, since signin never
    creates a user; see SignupRequest for that."""

    id_token: str


class OwnerSignupRequest(BaseModel):
    """POST /auth/signup/owner only -- the sole way a tenant (and its
    first user, as Owner) now comes into existence. There is no
    `register_as_admin` field here: every onboarding path is either this
    one (Owner, first user of a new company domain) or
    InvitationService.accept (Member/Admin, invited by an existing
    Owner/Admin)."""

    id_token: str
    username: Username | None = None
    tenant_name: str = Field(min_length=1, max_length=128)


class OwnerSignupTenant(BaseModel):
    id: uuid.UUID
    name: str
    domain: str


class OwnerSignupResponse(BaseModel):
    """No session is issued here -- the Owner signs in separately via
    POST /auth/login afterward, same as every other account-creation path
    except invite-accept."""

    tenant: OwnerSignupTenant


class AccountDeletionRequest(BaseModel):
    id_token: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    """Bearer token pair -- the frontend stores both and attaches
    access_token as `Authorization: Bearer <token>` on every request (see
    abyss_backend/abyss_frontend, which this mirrors exactly). There is no
    server-side session to invalidate; possession of a valid, unexpired
    token is the only thing that's ever checked.
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
