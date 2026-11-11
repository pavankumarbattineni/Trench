"""Sends transactional email -- tenant invitations and password-reset links.

Two transports, chosen by configuration:

- **Brevo HTTPS API** (TRENCH_CONFIG.BREVO set): used in production. Many
  hosts, Render's free tier included, block outbound SMTP ports
  (25/465/587), so SMTP from there can never connect; HTTPS (443) is open.
- **SMTP** (TRENCH_CONFIG.SMTP): the fallback, e.g. local development with
  a Gmail app password.
"""

from email.message import EmailMessage

import aiosmtplib
import httpx

from app.config import get_settings

BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"
_HTTP_TIMEOUT_SECONDS = 20


class EmailSendError(Exception):
    """The email provider rejected or failed the send."""


async def send_email(*, to: str, subject: str, html_body: str, text_body: str) -> None:
    """Sends one email. Raises on failure -- callers (InvitationService,
    PasswordResetService) decide how to surface that; this function has no
    retry/swallow logic of its own.
    """
    config = get_settings().TRENCH_CONFIG
    if config.BREVO is not None:
        await _send_via_brevo(
            to=to, subject=subject, html_body=html_body, text_body=text_body
        )
        return
    await _send_via_smtp(
        to=to, subject=subject, html_body=html_body, text_body=text_body
    )


async def _send_via_brevo(
    *, to: str, subject: str, html_body: str, text_body: str
) -> None:
    brevo = get_settings().TRENCH_CONFIG.BREVO
    payload = {
        "sender": {"email": brevo.from_address, "name": brevo.from_name},
        "to": [{"email": to}],
        "subject": subject,
        "htmlContent": html_body,
        "textContent": text_body,
    }
    headers = {"api-key": brevo.api_key, "accept": "application/json"}
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECONDS) as client:
        response = await client.post(BREVO_SEND_URL, json=payload, headers=headers)
    if response.status_code >= 400:
        # Brevo's error body ({"code", "message"}) names the problem, e.g. an
        # unverified sender or a blocked IP -- keep it for the logs.
        raise EmailSendError(
            f"Brevo rejected the email ({response.status_code}): {response.text[:300]}"
        )


async def _send_via_smtp(
    *, to: str, subject: str, html_body: str, text_body: str
) -> None:
    smtp = get_settings().TRENCH_CONFIG.SMTP

    message = EmailMessage()
    message["From"] = smtp.from_address
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    await aiosmtplib.send(
        message,
        hostname=smtp.host,
        port=smtp.port,
        username=smtp.username or None,
        password=smtp.password or None,
        start_tls=smtp.use_tls,
    )
