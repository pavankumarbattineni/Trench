"""Sends transactional email -- tenant invitations and password-reset links.

Two transports, chosen by configuration:

- **Gmail API over HTTPS** (TRENCH_CONFIG.GMAIL_API set): used in
  production. Many hosts, Render's free tier included, block outbound SMTP
  ports (25/465/587), so SMTP from there can never connect; HTTPS (443) is
  open. Mail is still sent as the same Gmail account -- only the transport
  changes.
- **SMTP** (TRENCH_CONFIG.SMTP): the fallback, e.g. local development with
  a Gmail app password.
"""

import asyncio
import base64
import time
from email.message import EmailMessage
from email.utils import formataddr

import aiosmtplib
import httpx

from app.config import get_settings

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
_HTTP_TIMEOUT_SECONDS = 20
# Refresh an access token this long before Google's stated expiry.
_TOKEN_EXPIRY_MARGIN_SECONDS = 60

_access_token: str | None = None
_access_token_expires_at = 0.0
_token_lock = asyncio.Lock()


class EmailSendError(Exception):
    """The email provider rejected or failed the send."""


def _build_message(
    *, sender: str, to: str, subject: str, html_body: str, text_body: str
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
    return message


async def send_email(*, to: str, subject: str, html_body: str, text_body: str) -> None:
    """Sends one email. Raises on failure -- callers (InvitationService,
    PasswordResetService) decide how to surface that; this function has no
    retry/swallow logic of its own.
    """
    if get_settings().TRENCH_CONFIG.GMAIL_API is not None:
        await _send_via_gmail_api(
            to=to, subject=subject, html_body=html_body, text_body=text_body
        )
        return
    await _send_via_smtp(
        to=to, subject=subject, html_body=html_body, text_body=text_body
    )


async def _gmail_access_token(client: httpx.AsyncClient) -> str:
    """A cached OAuth access token, refreshed from the long-lived refresh
    token when missing or about to expire (Google issues ~1-hour tokens)."""
    global _access_token, _access_token_expires_at
    async with _token_lock:
        if _access_token and time.monotonic() < _access_token_expires_at:
            return _access_token
        gmail = get_settings().TRENCH_CONFIG.GMAIL_API
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": gmail.client_id,
                "client_secret": gmail.client_secret,
                "refresh_token": gmail.refresh_token,
            },
        )
        if response.status_code >= 400:
            # e.g. {"error": "invalid_grant"}: the refresh token was revoked
            # or has expired -- re-run the one-time authorization.
            raise EmailSendError(
                f"Google token refresh failed ({response.status_code}): "
                f"{response.text[:300]}"
            )
        body = response.json()
        _access_token = body["access_token"]
        _access_token_expires_at = (
            time.monotonic()
            + int(body.get("expires_in", 3600))
            - _TOKEN_EXPIRY_MARGIN_SECONDS
        )
        return _access_token


def _reset_gmail_token_cache() -> None:
    global _access_token, _access_token_expires_at
    _access_token, _access_token_expires_at = None, 0.0


async def _send_via_gmail_api(
    *, to: str, subject: str, html_body: str, text_body: str
) -> None:
    gmail = get_settings().TRENCH_CONFIG.GMAIL_API
    message = _build_message(
        sender=formataddr((gmail.from_name, gmail.from_address)),
        to=to,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
        token = await _gmail_access_token(client)
        response = await client.post(
            GMAIL_SEND_URL,
            json={"raw": raw},
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code == 401:
            # Token revoked/expired early: refresh once and retry.
            _reset_gmail_token_cache()
            token = await _gmail_access_token(client)
            response = await client.post(
                GMAIL_SEND_URL,
                json={"raw": raw},
                headers={"Authorization": f"Bearer {token}"},
            )
    if response.status_code >= 400:
        raise EmailSendError(
            f"Gmail API rejected the email ({response.status_code}): "
            f"{response.text[:300]}"
        )


async def _send_via_smtp(
    *, to: str, subject: str, html_body: str, text_body: str
) -> None:
    smtp = get_settings().TRENCH_CONFIG.SMTP
    message = _build_message(
        sender=smtp.from_address,
        to=to,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )
    await aiosmtplib.send(
        message,
        hostname=smtp.host,
        port=smtp.port,
        username=smtp.username or None,
        password=smtp.password or None,
        start_tls=smtp.use_tls,
    )
