import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.service.llm_client_service import LLMClientService


class _FakeUser:
    def __init__(self):
        self.id = uuid.uuid4()
        self.model_id = None


class _FakeModel:
    def __init__(self):
        self.id = uuid.uuid4()
        self.model_name = "llama-x"
        self.provider_id = uuid.uuid4()


class _FakeProvider:
    def __init__(self, name: str, display_name: str | None = None):
        self.name = name
        self.display_name = display_name or name.capitalize()


class _FakeDB:
    def __init__(self, provider: _FakeProvider, model: _FakeModel):
        self._provider = provider
        self._model = model

    async def get(self, model_cls, _model_id):
        if model_cls.__name__ == "Provider":
            return self._provider
        return self._model


@pytest.mark.asyncio
async def test_resolve_for_user_raises_when_no_credential_for_a_byok_model():
    """No provider's models may be used without a valid API key for that
    provider -- a BYOK model selection with no matching UserCredential
    must hard-stop, never silently substitute the platform default."""
    user = _FakeUser()
    default_model = _FakeModel()
    provider = _FakeProvider("anthropic", display_name="Anthropic")
    db = _FakeDB(provider, default_model)

    with (
        patch(
            "app.service.llm_client_service.ProviderCatalogService.get_default",
            new=AsyncMock(return_value=default_model),
        ),
        patch(
            "app.service.llm_client_service.CredentialService.list_credentials",
            new=AsyncMock(return_value=[]),
        ),
    ):
        with pytest.raises(LLMClientService.MissingCredentialError) as exc_info:
            await LLMClientService.resolve_for_user(db, user)

    assert "Anthropic" in str(exc_info.value)


@pytest.mark.asyncio
async def test_personal_query_delegates_to_resolve_for_user():
    user = _FakeUser()
    db = _FakeDB(_FakeProvider("groq"), _FakeModel())

    with patch(
        "app.service.llm_client_service.LLMClientService.resolve_for_user",
        new=AsyncMock(return_value="sentinel"),
    ) as mock_resolve_for_user:
        result = await LLMClientService.resolve_for_knowledge(
            db, user, knowledge_type="personal", organization_id=None
        )
    assert result == "sentinel"
    mock_resolve_for_user.assert_awaited_once_with(db, user)


@pytest.mark.asyncio
async def test_company_query_uses_organization_credential_when_set():
    user = _FakeUser()
    default_model = _FakeModel()
    provider = _FakeProvider("openai")
    db = _FakeDB(provider, default_model)

    with (
        patch(
            "app.service.llm_client_service.ProviderCatalogService.get_default",
            new=AsyncMock(return_value=default_model),
        ),
        patch(
            "app.service.llm_client_service.OrganizationCredentialService.get_decrypted",
            new=AsyncMock(return_value="org-owned-key"),
        ),
    ):
        resolved = await LLMClientService.resolve_for_knowledge(
            db, user, knowledge_type="company", organization_id=uuid.uuid4()
        )
    assert resolved.api_key == "org-owned-key"
    assert resolved.provider_name == "openai"


@pytest.mark.asyncio
async def test_company_query_never_falls_back_to_a_members_personal_credential():
    """Even if the querying member has their own valid UserCredential, a
    company-knowledge query must not use it -- only OrganizationCredential
    or the platform default."""
    user = _FakeUser()
    default_model = _FakeModel()
    provider = _FakeProvider("openai")
    db = _FakeDB(provider, default_model)

    with (
        patch(
            "app.service.llm_client_service.ProviderCatalogService.get_default",
            new=AsyncMock(return_value=default_model),
        ),
        patch(
            "app.service.llm_client_service.OrganizationCredentialService.get_decrypted",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.service.llm_client_service.CredentialService.list_credentials",
            new=AsyncMock(
                side_effect=AssertionError(
                    "must not look up the member's personal credentials for"
                    " a company query"
                )
            ),
        ),
        patch(
            "app.service.llm_client_service.get_settings"
        ) as mock_get_settings,
    ):
        mock_get_settings.return_value.TRENCH_CONFIG.GROQ.api_key = "platform-groq-key"
        resolved = await LLMClientService.resolve_for_knowledge(
            db, user, knowledge_type="company", organization_id=uuid.uuid4()
        )
    # Falls back to the platform Groq default rather than ever calling
    # CredentialService.list_credentials for this user.
    assert resolved.provider_name == "groq"
    assert resolved.api_key == "platform-groq-key"


@pytest.mark.asyncio
async def test_company_query_with_groq_default_model_skips_org_credential_lookup():
    user = _FakeUser()
    default_model = _FakeModel()
    provider = _FakeProvider("groq")
    db = _FakeDB(provider, default_model)

    with (
        patch(
            "app.service.llm_client_service.ProviderCatalogService.get_default",
            new=AsyncMock(return_value=default_model),
        ),
        patch(
            "app.service.llm_client_service.OrganizationCredentialService.get_decrypted",
            new=AsyncMock(
                side_effect=AssertionError("must not be called for a groq model")
            ),
        ),
        patch("app.service.llm_client_service.get_settings") as mock_get_settings,
    ):
        mock_get_settings.return_value.TRENCH_CONFIG.GROQ.api_key = "platform-groq-key"
        resolved = await LLMClientService.resolve_for_knowledge(
            db, user, knowledge_type="company", organization_id=uuid.uuid4()
        )
    assert resolved.provider_name == "groq"
    assert resolved.api_key == "platform-groq-key"
