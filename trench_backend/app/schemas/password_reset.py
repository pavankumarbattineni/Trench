from pydantic import BaseModel, EmailStr, Field, model_validator


class RequestPasswordResetRequest(BaseModel):
    email: EmailStr


class ConfirmPasswordResetRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8)
    confirm_password: str = Field(min_length=8)

    @model_validator(mode="after")
    def _passwords_must_match(self) -> "ConfirmPasswordResetRequest":
        if self.new_password != self.confirm_password:
            raise ValueError("new_password and confirm_password must match")
        return self
