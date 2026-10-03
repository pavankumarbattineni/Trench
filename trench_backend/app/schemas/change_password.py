from pydantic import BaseModel, Field, model_validator


class ChangePasswordRequest(BaseModel):
    """For the authenticated Settings > Change Password flow -- distinct
    from the Forgot Password flow's RequestPasswordResetRequest/
    ConfirmPasswordResetRequest (see app/schemas/password_reset.py), which
    is for a signed-out user with no current password to prove."""

    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8)
    confirm_new_password: str = Field(min_length=8)

    @model_validator(mode="after")
    def _new_passwords_must_match(self) -> "ChangePasswordRequest":
        if self.new_password != self.confirm_new_password:
            raise ValueError("new_password and confirm_new_password must match")
        return self
