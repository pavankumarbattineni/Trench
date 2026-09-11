import uuid

from pydantic import BaseModel


class ModelResponse(BaseModel):
    id: uuid.UUID
    provider_name: str
    model_name: str
    display_name: str
    is_platform_default: bool


class ConfigResponse(BaseModel):
    llm_models: list[ModelResponse]
