from unittest.mock import AsyncMock, patch

import pytest

from app.utils.email import send_email


@pytest.mark.asyncio
async def test_send_email_calls_aiosmtplib_send_with_expected_message():
    with patch("app.utils.email.aiosmtplib.send", new=AsyncMock()) as mock_send:
        await send_email(
            to="test.recipient@example.com",
            subject="Test subject",
            html_body="<p>hello</p>",
            text_body="hello",
        )
    assert mock_send.await_count == 1
    message = mock_send.call_args.args[0]
    assert message["To"] == "test.recipient@example.com"
    assert message["Subject"] == "Test subject"


@pytest.mark.asyncio
async def test_send_email_propagates_smtp_errors():
    with patch(
        "app.utils.email.aiosmtplib.send",
        new=AsyncMock(side_effect=OSError("smtp unreachable")),
    ):
        with pytest.raises(OSError):
            await send_email(
                to="test.recipient@example.com",
                subject="x",
                html_body="x",
                text_body="x",
            )
