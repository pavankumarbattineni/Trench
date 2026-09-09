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
