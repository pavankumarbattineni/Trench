from pydantic import BaseModel


class ErrorResponse(BaseModel):
    status_code: int
    status: str
    message: str
