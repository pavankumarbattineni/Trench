"""Output limits and failure messages for chat answers.

- Groq's default output limit (3,072 tokens, shared with the model's hidden
  reasoning) silently cut long answers off mid-sentence; the limit is now
  sized to the room the prompt leaves in the per-minute token budget.
- An answer a model stopped at its output limit ends with TRUNCATION_NOTE
  rather than presenting a cut-off answer as complete.
- A failed turn shows a plain explanation, not a raw provider error dump.
"""

from types import SimpleNamespace

import pytest

from app.service import llm_client_service
from app.service.chat_service import _user_facing_error
from app.service.llm_client_service import (
    _GROQ_MAX_OUTPUT_TOKENS,
    _GROQ_MIN_OUTPUT_TOKENS,
    _TRUNCATED,
    TRUNCATION_NOTE,
    LLMClientService,
    ResolvedModel,
    _groq_max_completion_tokens,
)


def _budget(monkeypatch, tokens_per_minute: int) -> None:
    settings = SimpleNamespace(
        TRENCH_CONFIG=SimpleNamespace(
            GROQ=SimpleNamespace(tokens_per_minute=tokens_per_minute)
        )
    )
    monkeypatch.setattr(llm_client_service, "get_settings", lambda: settings)


def test_groq_output_limit_uses_the_room_left_in_the_budget(monkeypatch):
    _budget(monkeypatch, 8000)
    limit = _groq_max_completion_tokens(
        "short system prompt", [{"role": "user", "content": "hi"}]
    )
    # Nearly the whole 8,000-token budget is available for a tiny prompt --
    # well above Groq's 3,072 default that was truncating answers.
    assert 7000 < limit < 8000


def test_groq_output_limit_shrinks_as_the_prompt_grows(monkeypatch):
    _budget(monkeypatch, 8000)
    small = _groq_max_completion_tokens(
        "x", [{"role": "user", "content": "word " * 100}]
    )
    large = _groq_max_completion_tokens(
        "x", [{"role": "user", "content": "word " * 3000}]
    )
    assert large < small


def test_groq_output_limit_never_drops_below_the_minimum(monkeypatch):
    _budget(monkeypatch, 8000)
    huge = _groq_max_completion_tokens(
        "x", [{"role": "user", "content": "word " * 20000}]
    )
    assert huge == _GROQ_MIN_OUTPUT_TOKENS


def test_groq_output_limit_is_capped_on_a_large_budget(monkeypatch):
    _budget(monkeypatch, 1_000_000)
    assert _groq_max_completion_tokens("x", []) == _GROQ_MAX_OUTPUT_TOKENS


async def _collect(monkeypatch, provider_deltas: list[str]) -> str:
    async def _fake_provider(resolved, *, system_prompt, messages):
        for delta in provider_deltas:
            yield delta

    monkeypatch.setattr(LLMClientService, "_stream_groq", _fake_provider)
    resolved = ResolvedModel(provider_name="groq", model_name="m", api_key="k")
    text = ""
    async for delta in LLMClientService.stream_generate(
        resolved, system_prompt="s", messages=[]
    ):
        text += delta
    return text


@pytest.mark.asyncio
async def test_truncated_answer_ends_with_the_note(monkeypatch):
    text = await _collect(monkeypatch, ["The answer was going", " well", _TRUNCATED])
    assert text == "The answer was going well" + TRUNCATION_NOTE


@pytest.mark.asyncio
async def test_complete_answer_has_no_note(monkeypatch):
    text = await _collect(monkeypatch, ["A complete", " answer."])
    assert text == "A complete answer."
    assert _TRUNCATED not in text


class _ProviderError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(
            f"Error code: {status_code} - {{'error': {{'message': '{message}'}}}}"
        )
        self.status_code = status_code


@pytest.mark.parametrize(
    ("error", "expected_phrase"),
    [
        (
            _ProviderError(413, "Request too large ... tokens per minute (TPM)"),
            "too large",
        ),
        (
            _ProviderError(429, "Rate limit reached ... tokens per minute (TPM)"),
            "too many requests",
        ),
        (
            _ProviderError(
                429,
                "You exceeded your current quota ... limit: 0, model: gemini-4-argon",
            ),
            "quota exceeded",
        ),
        (_ProviderError(429, "insufficient_quota"), "quota exceeded"),
        (_ProviderError(429, "Too many requests"), "too many requests"),
        (_ProviderError(401, "Invalid API key"), "API key"),
        (RuntimeError("boom"), "Something went wrong"),
    ],
)
def test_failed_turns_show_a_plain_explanation(error, expected_phrase):
    message = _user_facing_error(error)
    assert expected_phrase.lower() in message.lower()
    # Never the raw provider dump.
    assert "Error code" not in message
    assert "{" not in message
