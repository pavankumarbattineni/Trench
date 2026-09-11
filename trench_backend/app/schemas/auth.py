from typing import Annotated

from pydantic import BaseModel, Field

Username = Annotated[
    str, Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_-]+$")
]


class FirebaseSessionRequest(BaseModel):
    id_token: str
    username: Username | None = None


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
