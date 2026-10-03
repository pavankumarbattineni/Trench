from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.utils.firebase import verify_user_password


def _mock_http_client(status_code: int, json_body: dict | None = None) -> Mock:
    response = Mock(status_code=status_code)
    response.json.return_value = json_body or {}
    response.text = str(json_body or {})
    post = AsyncMock(return_value=response)
    client_instance = Mock()
    client_instance.post = post
    client_instance.__aenter__ = AsyncMock(return_value=client_instance)
    client_instance.__aexit__ = AsyncMock(return_value=False)
    return client_instance, post


@pytest.mark.asyncio
async def test_verify_user_password_returns_true_for_a_200_response():
    client_instance, post = _mock_http_client(200)
    with (
        patch("app.utils.firebase.httpx.AsyncClient", return_value=client_instance),
        patch("app.utils.firebase.get_settings") as mock_settings,
    ):
        mock_settings.return_value.TRENCH_CONFIG.FIREBASE.web_api_key = "fake-key"
        result = await verify_user_password("user@example.com", "correct-password")

    assert result is True
    post.assert_awaited_once()
    _, kwargs = post.call_args
    assert kwargs["params"] == {"key": "fake-key"}
    assert kwargs["json"] == {
        "email": "user@example.com",
        "password": "correct-password",
        "returnSecureToken": False,
    }


@pytest.mark.asyncio
async def test_verify_user_password_returns_false_for_a_400_response():
    client_instance, _post = _mock_http_client(
        400, {"error": {"message": "INVALID_LOGIN_CREDENTIALS"}}
    )
    with (
        patch("app.utils.firebase.httpx.AsyncClient", return_value=client_instance),
        patch("app.utils.firebase.get_settings") as mock_settings,
    ):
        mock_settings.return_value.TRENCH_CONFIG.FIREBASE.web_api_key = "fake-key"
        result = await verify_user_password("user@example.com", "wrong-password")

    assert result is False


@pytest.mark.asyncio
async def test_genuine_wrong_password_is_not_logged_as_an_error(caplog):
    """An actual wrong password is routine, expected user behavior --
    logging it at ERROR on every failed attempt would be alert-fatigue
    noise, not a signal anyone should act on."""
    client_instance, _post = _mock_http_client(
        400, {"error": {"message": "INVALID_LOGIN_CREDENTIALS"}}
    )
    with (
        patch("app.utils.firebase.httpx.AsyncClient", return_value=client_instance),
        patch("app.utils.firebase.get_settings") as mock_settings,
        caplog.at_level("ERROR", logger="app.utils.firebase"),
    ):
        mock_settings.return_value.TRENCH_CONFIG.FIREBASE.web_api_key = "fake-key"
        result = await verify_user_password("user@example.com", "wrong-password")

    assert result is False
    assert caplog.records == []


@pytest.mark.asyncio
async def test_misconfiguration_failure_is_logged_as_an_error(caplog):
    """A bad/restricted web_api_key (or email/password sign-in disabled
    for the Firebase project) must be loud -- it's indistinguishable from
    a wrong password to the end user, but it is not one, and nobody can
    diagnose it from the outside without this log line."""
    client_instance, _post = _mock_http_client(
        400, {"error": {"message": "API key not valid. Please pass a valid API key."}}
    )
    with (
        patch("app.utils.firebase.httpx.AsyncClient", return_value=client_instance),
        patch("app.utils.firebase.get_settings") as mock_settings,
        caplog.at_level("ERROR", logger="app.utils.firebase"),
    ):
        mock_settings.return_value.TRENCH_CONFIG.FIREBASE.web_api_key = "bad-key"
        result = await verify_user_password("user@example.com", "whatever")

    assert result is False
    assert len(caplog.records) == 1
    assert "API key not valid" in caplog.records[0].message
