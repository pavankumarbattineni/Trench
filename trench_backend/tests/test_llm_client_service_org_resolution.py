"""Regression tests for LLMClientService's deliberate personal-vs-tenant
credential asymmetry. Both scopes now live in one `credentials` table and
are read through one CredentialService.get_decrypted (scoped by exactly
one of user_id/tenant_id) -- these tests pin that the *resolution* rules
still differ:

- personal: the user's own key, or a hard MissingCredentialError;
- company/tenant: the tenant's key, else silently the platform default,
  never the querying member's personal key.
"""

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


def _get_decrypted_by_scope(*, personal, tenant):
    """A stand-in for CredentialService.get_decrypted that answers
    per-scope. Either value may be an Exception, raised if that scope is
    ever looked up."""

    async def _fake(_db, *, provider_type, user_id=None, tenant_id=None):
        assert (user_id is None) != (tenant_id is None)
        value = personal if user_id is not None else tenant
        if isinstance(value, Exception):
            raise value
        return value

    return AsyncMock(side_effect=_fake)


_GET_DECRYPTED = "app.service.llm_client_service.CredentialService.get_decrypted"
_GET_DEFAULT = "app.service.llm_client_service.ProviderCatalogService.get_default"


@pytest.mark.asyncio
async def test_resolve_for_user_raises_when_no_credential_for_a_byok_model():
    """No provider's models may be used without a valid API key for that
    provider -- a BYOK model selection with no matching personal
    credential must hard-stop, never silently substitute the platform
    default."""
    user = _FakeUser()
    default_model = _FakeModel()
    provider = _FakeProvider("anthropic", display_name="Anthropic")
    db = _FakeDB(provider, default_model)

    with (
        patch(_GET_DEFAULT, new=AsyncMock(return_value=default_model)),
        patch(
            _GET_DECRYPTED,
            new=_get_decrypted_by_scope(
                personal=None,
                tenant=AssertionError("a personal lookup must not hit tenant scope"),
            ),
        ),
    ):
        with pytest.raises(LLMClientService.MissingCredentialError) as exc_info:
            await LLMClientService.resolve_for_user(db, user)

    assert "Anthropic" in str(exc_info.value)


@pytest.mark.asyncio
async def test_resolve_for_user_uses_the_personal_credential():
    user = _FakeUser()
    default_model = _FakeModel()
    db = _FakeDB(_FakeProvider("openai"), default_model)

    with (
        patch(_GET_DEFAULT, new=AsyncMock(return_value=default_model)),
        patch(
            _GET_DECRYPTED,
            new=_get_decrypted_by_scope(
                personal="personal-key",
                tenant=AssertionError("a personal lookup must not hit tenant scope"),
            ),
        ),
    ):
        resolved = await LLMClientService.resolve_for_user(db, user)

    assert resolved.api_key == "personal-key"
    assert resolved.provider_name == "openai"


@pytest.mark.asyncio
async def test_personal_query_delegates_to_resolve_for_user():
    user = _FakeUser()
    db = _FakeDB(_FakeProvider("groq"), _FakeModel())

    with patch(
        "app.service.llm_client_service.LLMClientService.resolve_for_user",
        new=AsyncMock(return_value="sentinel"),
    ) as mock_resolve_for_user:
        result = await LLMClientService.resolve_for_knowledge(
            db, user, knowledge_type="personal", tenant_id=None
        )
    assert result == "sentinel"
    mock_resolve_for_user.assert_awaited_once_with(db, user)


@pytest.mark.asyncio
async def test_company_query_uses_tenant_credential_when_set():
    user = _FakeUser()
    default_model = _FakeModel()
    provider = _FakeProvider("openai")
    db = _FakeDB(provider, default_model)

    with (
        patch(_GET_DEFAULT, new=AsyncMock(return_value=default_model)),
        patch(
            _GET_DECRYPTED,
            new=_get_decrypted_by_scope(
                personal=AssertionError("must not use the member's personal key"),
                tenant="tenant-owned-key",
            ),
        ),
    ):
        resolved = await LLMClientService.resolve_for_knowledge(
            db, user, knowledge_type="company", tenant_id=uuid.uuid4()
        )
    assert resolved.api_key == "tenant-owned-key"
    assert resolved.provider_name == "openai"


@pytest.mark.asyncio
async def test_company_query_never_falls_back_to_a_members_personal_credential():
    """Even if the querying member has their own valid personal
    credential, a company-knowledge query must not use it -- only the
    tenant's credential or the platform default. The personal scope must
    never even be looked up."""
    user = _FakeUser()
    default_model = _FakeModel()
    provider = _FakeProvider("openai")
    db = _FakeDB(provider, default_model)

    with (
        patch(_GET_DEFAULT, new=AsyncMock(return_value=default_model)),
        patch(
            _GET_DECRYPTED,
            new=_get_decrypted_by_scope(
                personal=AssertionError(
                    "must not look up the member's personal credentials for"
                    " a company query"
                ),
                tenant=None,
            ),
        ) as mock_get_decrypted,
        patch("app.service.llm_client_service.get_settings") as mock_get_settings,
    ):
        mock_get_settings.return_value.TRENCH_CONFIG.GROQ.api_key = "platform-groq-key"
        resolved = await LLMClientService.resolve_for_knowledge(
            db, user, knowledge_type="company", tenant_id=uuid.uuid4()
        )
    # Falls back to the platform Groq default rather than ever looking up
    # this user's personal credential.
    assert resolved.provider_name == "groq"
    assert resolved.api_key == "platform-groq-key"
    lookups = mock_get_decrypted.await_args_list
    assert all(call.kwargs.get("user_id") is None for call in lookups)


@pytest.mark.asyncio
async def test_company_query_with_groq_default_model_skips_tenant_credential_lookup():
    user = _FakeUser()
    default_model = _FakeModel()
    provider = _FakeProvider("groq")
    db = _FakeDB(provider, default_model)

    with (
        patch(_GET_DEFAULT, new=AsyncMock(return_value=default_model)),
        patch(
            _GET_DECRYPTED,
            new=AsyncMock(
                side_effect=AssertionError("must not be called for a groq model")
            ),
        ),
        patch("app.service.llm_client_service.get_settings") as mock_get_settings,
    ):
        mock_get_settings.return_value.TRENCH_CONFIG.GROQ.api_key = "platform-groq-key"
        resolved = await LLMClientService.resolve_for_knowledge(
            db, user, knowledge_type="company", tenant_id=uuid.uuid4()
        )
    assert resolved.provider_name == "groq"
    assert resolved.api_key == "platform-groq-key"
