import uuid

from pydantic import BaseModel


class ModelResponse(BaseModel):
    id: uuid.UUID
    provider_name: str
    model_name: str
    display_name: str
    is_platform_default: bool
    # Whether this model needs a BYOK credential at all (false only for the
    # platform-owned Groq default), and whether the *current* user already
    # has one saved for it. The frontend uses these to disable/blur a model
    # the user can't actually use yet -- but this is a UX convenience only;
    # the backend independently re-validates on selection (see
    # UserPreferenceService.update_model), so it's never the sole guard.
    requires_api_key: bool
    has_credential: bool


class ConfigResponse(BaseModel):
    llm_models: list[ModelResponse]
