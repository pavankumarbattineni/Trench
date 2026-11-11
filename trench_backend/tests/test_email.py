"""Email transport selection: Brevo HTTPS API when configured, else SMTP."""

import json
from types import SimpleNamespace

import httpx
import pytest

from app.config import BrevoConfig, get_settings
from app.utils import email as email_module


def _settings(brevo: BrevoConfig | None):
    config = get_settings().TRENCH_CONFIG.model_copy(update={"BREVO": brevo})
    return SimpleNamespace(TRENCH_CONFIG=config)


def _client_with(transport: httpx.MockTransport):
    real_client = httpx.AsyncClient

    def factory(**kwargs):
        return real_client(transport=transport, **kwargs)

    return factory


@pytest.mark.asyncio
async def test_brevo_is_used_when_configured(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["api_key"] = request.headers.get("api-key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"messageId": "<abc@brevo>"})

    brevo = BrevoConfig(api_key="test-key", from_address="noreply@example.com")
    monkeypatch.setattr(email_module, "get_settings", lambda: _settings(brevo))
    monkeypatch.setattr(
        email_module.httpx, "AsyncClient", _client_with(httpx.MockTransport(handler))
    )

    async def _no_smtp(*args, **kwargs):
        raise AssertionError("SMTP must not be used when Brevo is configured")

    monkeypatch.setattr(email_module.aiosmtplib, "send", _no_smtp)

    await email_module.send_email(
        to="invitee@example.com",
        subject="Join Trench",
        html_body="<p>hi</p>",
        text_body="hi",
    )

    assert seen["url"] == email_module.BREVO_SEND_URL
    assert seen["api_key"] == "test-key"
    assert seen["body"]["sender"] == {"email": "noreply@example.com", "name": "Trench"}
    assert seen["body"]["to"] == [{"email": "invitee@example.com"}]
    assert seen["body"]["subject"] == "Join Trench"
    assert seen["body"]["htmlContent"] == "<p>hi</p>"
    assert seen["body"]["textContent"] == "hi"


@pytest.mark.asyncio
async def test_brevo_rejection_raises_with_the_provider_reason(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"code": "invalid_parameter", "message": "sender not verified"}
        )

    brevo = BrevoConfig(api_key="k", from_address="unverified@example.com")
    monkeypatch.setattr(email_module, "get_settings", lambda: _settings(brevo))
    monkeypatch.setattr(
        email_module.httpx, "AsyncClient", _client_with(httpx.MockTransport(handler))
    )

    with pytest.raises(email_module.EmailSendError, match="sender not verified"):
        await email_module.send_email(
            to="x@example.com", subject="s", html_body="h", text_body="t"
        )


@pytest.mark.asyncio
async def test_smtp_is_used_when_brevo_is_not_configured(monkeypatch):
    sent = {}

    async def fake_send(message, **kwargs):
        sent["to"] = message["To"]
        sent["hostname"] = kwargs["hostname"]

    monkeypatch.setattr(email_module, "get_settings", lambda: _settings(None))
    monkeypatch.setattr(email_module.aiosmtplib, "send", fake_send)

    await email_module.send_email(
        to="x@example.com", subject="s", html_body="h", text_body="t"
    )

    assert sent["to"] == "x@example.com"
    assert sent["hostname"] == get_settings().TRENCH_CONFIG.SMTP.host
