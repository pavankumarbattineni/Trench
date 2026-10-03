from unittest.mock import patch

import pytest

from app.service.jev_service import JevService


@pytest.mark.asyncio
async def test_ask_noul_uses_stub_fallback_when_typesafe_not_configured():
    """No TRENCH_CONFIG.TYPESAFE.api_key set -- real JEV is never called,
    the caller-supplied heuristic decides instead."""
    with patch(
        "app.service.jev_service.get_settings"
    ) as mock_get_settings:
        mock_get_settings.return_value.TRENCH_CONFIG.TYPESAFE.api_key = None

        result = await JevService.ask_noul(
            "is this sufficient?", stub_fallback=lambda: 0.73
        )

    assert result == 0.73


def test_is_configured_false_without_an_api_key():
    with patch("app.service.jev_service.get_settings") as mock_get_settings:
        mock_get_settings.return_value.TRENCH_CONFIG.TYPESAFE.api_key = None
        assert JevService.is_configured() is False


def test_is_configured_true_with_an_api_key():
    with patch("app.service.jev_service.get_settings") as mock_get_settings:
        mock_get_settings.return_value.TRENCH_CONFIG.TYPESAFE.api_key = "sk-typesafe-x"
        assert JevService.is_configured() is True
