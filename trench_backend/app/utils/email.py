"""Sends transactional email over SMTP -- tenant invitations and
password-reset links. No third-party email API: TRENCH_CONFIG.SMTP holds
a plain SMTP account's credentials, following the same
TRENCH_CONFIG.<SECTION> pattern as FIREBASE/GROQ/PINECONE.
"""

from email.message import EmailMessage

import aiosmtplib

from app.config import get_settings


async def send_email(*, to: str, subject: str, html_body: str, text_body: str) -> None:
    """Sends one email. Raises whatever aiosmtplib raises on failure --
    callers (InvitationService, PasswordResetService) decide how to
    surface that; this function has no retry/swallow logic of its own.
    """
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
