"""Email transport selection: Gmail API over HTTPS when configured, else SMTP."""

import base64
import email
import json
from email import policy
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest

from app.config import GmailApiConfig, get_settings
from app.utils import email as email_module

GMAIL = GmailApiConfig(
    client_id="cid.apps.googleusercontent.com",
    client_secret="secret",
    refresh_token="refresh-123",
    from_address="sender@gmail.com",
)


def _settings(gmail: GmailApiConfig | None):
    config = get_settings().TRENCH_CONFIG.model_copy(update={"GMAIL_API": gmail})
    return SimpleNamespace(TRENCH_CONFIG=config)


def _use_transport(monkeypatch, handler):
    real_client = httpx.AsyncClient

    def factory(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(email_module.httpx, "AsyncClient", factory)


@pytest.fixture(autouse=True)
def _fresh_token_cache():
    email_module._reset_gmail_token_cache()
    yield
    email_module._reset_gmail_token_cache()


@pytest.fixture
def _no_smtp(monkeypatch):
    async def fail(*args, **kwargs):
        raise AssertionError("SMTP must not be used when the Gmail API is configured")

    monkeypatch.setattr(email_module.aiosmtplib, "send", fail)


@pytest.mark.asyncio
async def test_gmail_api_refreshes_a_token_and_sends_the_message(monkeypatch, _no_smtp):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if str(request.url) == email_module.GOOGLE_TOKEN_URL:
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["refresh_token"]
            assert form["refresh_token"] == ["refresh-123"]
            assert form["client_id"] == ["cid.apps.googleusercontent.com"]
            return httpx.Response(
                200, json={"access_token": "at-1", "expires_in": 3599}
            )
        assert request.headers["Authorization"] == "Bearer at-1"
        raw = json.loads(request.content)["raw"]
        message = email.message_from_bytes(
            base64.urlsafe_b64decode(raw), policy=policy.default
        )
        assert message["To"] == "invitee@example.com"
        assert message["From"] == "Trench <sender@gmail.com>"
        assert message["Subject"] == "Join Trench"
        assert "https://example.com/invite" in message.get_body(("html",)).get_content()
        return httpx.Response(200, json={"id": "msg-1"})

    monkeypatch.setattr(email_module, "get_settings", lambda: _settings(GMAIL))
    _use_transport(monkeypatch, handler)

    await email_module.send_email(
        to="invitee@example.com",
        subject="Join Trench",
        html_body='<a href="https://example.com/invite">Accept</a>',
        text_body="Accept: https://example.com/invite",
    )

    assert calls == [email_module.GOOGLE_TOKEN_URL, email_module.GMAIL_SEND_URL]


@pytest.mark.asyncio
async def test_access_token_is_cached_between_sends(monkeypatch, _no_smtp):
    token_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_requests
        if str(request.url) == email_module.GOOGLE_TOKEN_URL:
            token_requests += 1
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3599})
        return httpx.Response(200, json={"id": "m"})

    monkeypatch.setattr(email_module, "get_settings", lambda: _settings(GMAIL))
    _use_transport(monkeypatch, handler)

    for _ in range(3):
        await email_module.send_email(
            to="a@b.c", subject="s", html_body="h", text_body="t"
        )

    assert token_requests == 1


@pytest.mark.asyncio
async def test_a_rejected_access_token_is_refreshed_once_and_retried(
    monkeypatch, _no_smtp
):
    sends = []

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == email_module.GOOGLE_TOKEN_URL:
            token = "fresh" if sends else "stale"
            return httpx.Response(200, json={"access_token": token, "expires_in": 3599})
        sends.append(request.headers["Authorization"])
        if request.headers["Authorization"] == "Bearer stale":
            return httpx.Response(401, json={"error": "invalid token"})
        return httpx.Response(200, json={"id": "m"})

    monkeypatch.setattr(email_module, "get_settings", lambda: _settings(GMAIL))
    _use_transport(monkeypatch, handler)

    await email_module.send_email(to="a@b.c", subject="s", html_body="h", text_body="t")

    assert sends == ["Bearer stale", "Bearer fresh"]


@pytest.mark.asyncio
async def test_a_revoked_refresh_token_raises_with_googles_reason(
    monkeypatch, _no_smtp
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    monkeypatch.setattr(email_module, "get_settings", lambda: _settings(GMAIL))
    _use_transport(monkeypatch, handler)

    with pytest.raises(email_module.EmailSendError, match="invalid_grant"):
        await email_module.send_email(
            to="a@b.c", subject="s", html_body="h", text_body="t"
        )


@pytest.mark.asyncio
async def test_smtp_is_used_when_the_gmail_api_is_not_configured(monkeypatch):
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
