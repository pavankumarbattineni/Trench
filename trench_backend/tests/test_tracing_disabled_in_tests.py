"""Test runs must never send traces to the real LangSmith project, even
when the developer's .env turns tracing on (see conftest.py)."""

from langsmith.utils import tracing_is_enabled

from app.utils.tracing import configure_tracing


def test_tracing_is_off_in_tests_even_after_loading_dotenv():
    configure_tracing()  # what app startup does: load .env
    assert tracing_is_enabled() is False
