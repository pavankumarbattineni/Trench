import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field

Username = Annotated[
    str, Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_-]+$")
]


class FirebaseSessionRequest(BaseModel):
    """POST /auth/login (signin) only -- no username, since signin never
    creates a user; see SignupRequest for that."""

    id_token: str


class SignupRequest(BaseModel):
    """POST /auth/signup only.

    `register_as_admin` is a request, not a grant -- the backend only ever
    honors it when no Trench administrator exists yet (see
    UserService.create_user). There is no raw `role` field a client could
    set directly; "admin" is reachable only through this one bootstrap
    path.
    """

    id_token: str
    username: Username | None = None
    register_as_admin: bool = False


class SignupResponse(BaseModel):
    id: uuid.UUID
    email: str
    username: str
    role: str
    created_at: datetime


class AdminStatusResponse(BaseModel):
    admin_exists: bool


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
