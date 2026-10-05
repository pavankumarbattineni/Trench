"""condense_query and reformulate_query: both use the platform default
model (never the user's selected model/BYOK credential -- these are cheap
internal rewrite steps, see LLMClientService.resolve_platform_default),
reformulate_query always rewrites original_condensed_query rather than
compounding onto a previous reformulation, and both window conversation
history to MAX_HISTORY_MESSAGES."""

from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import HumanMessage

from app.graph.rag_graph import (
    MAX_HISTORY_MESSAGES,
    condense_query,
    generate,
    reformulate_query,
)


def _config():
    return {"configurable": {"db": object(), "user": object()}}


@pytest.mark.asyncio
async def test_condense_query_sets_both_fields_to_the_raw_query_with_no_history():
    state = {"query": "What's our refund policy?", "messages": [{"role": "user"}]}

    result = await condense_query(state, _config())

    assert result == {
        "condensed_query": "What's our refund policy?",
        "original_condensed_query": "What's our refund policy?",
    }


@pytest.mark.asyncio
async def test_condense_query_sets_both_fields_to_the_same_rewritten_value():
    async def _fake_stream(_resolved, *, system_prompt, messages):
        yield "Standalone rewritten question?"

    state = {
        "query": "What about last month?",
        "messages": [
            {"role": "user", "content": "What's our refund policy?"},
            {"role": "assistant", "content": "Refunds take 5 days."},
            {"role": "user", "content": "What about last month?"},
        ],
    }
    with (
        patch(
            "app.graph.rag_graph.LLMClientService.resolve_platform_default",
            AsyncMock(return_value="resolved-model"),
        ),
        patch("app.graph.rag_graph.LLMClientService.stream_generate", _fake_stream),
    ):
        result = await condense_query(state, _config())

    assert result == {
        "condensed_query": "Standalone rewritten question?",
        "original_condensed_query": "Standalone rewritten question?",
    }


@pytest.mark.asyncio
async def test_condense_query_uses_the_platform_default_not_byok():
    resolve_platform_default = AsyncMock(return_value="resolved-model")

    async def _fake_stream(_resolved, *, system_prompt, messages):
        yield "rewritten"

    state = {
        "query": "follow-up",
        "messages": [
            {"role": "user", "content": "a"},
            {"role": "user", "content": "follow-up"},
        ],
    }
    with (
        patch(
            "app.graph.rag_graph.LLMClientService.resolve_platform_default",
            resolve_platform_default,
        ),
        patch("app.graph.rag_graph.LLMClientService.stream_generate", _fake_stream),
        patch(
            "app.graph.rag_graph.LLMClientService.resolve_for_knowledge",
            AsyncMock(side_effect=AssertionError("must not use the user's model")),
        ),
    ):
        await condense_query(state, _config())

    resolve_platform_default.assert_awaited_once()


@pytest.mark.asyncio
async def test_condense_query_falls_back_to_raw_query_when_resolution_fails():
    state = {
        "query": "follow-up",
        "messages": [
            {"role": "user", "content": "a"},
            {"role": "user", "content": "follow-up"},
        ],
    }
    with patch(
        "app.graph.rag_graph.LLMClientService.resolve_platform_default",
        AsyncMock(side_effect=RuntimeError("no default configured")),
    ):
        result = await condense_query(state, _config())

    assert result == {
        "condensed_query": "follow-up",
        "original_condensed_query": "follow-up",
    }


@pytest.mark.asyncio
async def test_condense_query_windows_history_to_max_history_messages():
    """history_text is built via getattr(m, "type"/"content", ...) --
    designed for the LangChain BaseMessage objects the checkpointer
    actually restores into state["messages"] (see _to_provider_message's
    own docstring), not plain dicts -- so this test uses HumanMessage to
    exercise real attribute access rather than silently falling back to
    getattr's defaults."""
    captured = {}

    async def _fake_stream(_resolved, *, system_prompt, messages):
        captured["prompt"] = messages[0]["content"]
        yield "rewritten"

    # MAX_HISTORY_MESSAGES + 5 prior turns, each uniquely identifiable.
    prior = [HumanMessage(content=f"turn-{i}") for i in range(MAX_HISTORY_MESSAGES + 5)]
    state = {
        "query": "follow-up",
        "messages": [*prior, HumanMessage(content="follow-up")],
    }
    with (
        patch(
            "app.graph.rag_graph.LLMClientService.resolve_platform_default",
            AsyncMock(return_value="resolved-model"),
        ),
        patch("app.graph.rag_graph.LLMClientService.stream_generate", _fake_stream),
    ):
        await condense_query(state, _config())

    prompt = captured["prompt"]
    assert "turn-0" not in prompt  # oldest turns fell outside the window
    assert f"turn-{MAX_HISTORY_MESSAGES + 4}" in prompt  # most recent prior turn


@pytest.mark.asyncio
async def test_reformulate_query_rewrites_original_not_the_previous_reformulation():
    captured = {}

    async def _fake_stream(_resolved, *, system_prompt, messages):
        captured["prompt"] = messages[0]["content"]
        yield "second rewrite"

    state = {
        "original_condensed_query": "original standalone question",
        # A prior reformulate_query call already overwrote condensed_query
        # with a first rewrite -- the second retry must not build on this.
        "condensed_query": "first rewrite, already once reformulated",
    }
    with (
        patch(
            "app.graph.rag_graph.LLMClientService.resolve_platform_default",
            AsyncMock(return_value="resolved-model"),
        ),
        patch("app.graph.rag_graph.LLMClientService.stream_generate", _fake_stream),
    ):
        result = await reformulate_query(state, _config())

    assert "original standalone question" in captured["prompt"]
    assert "first rewrite" not in captured["prompt"]
    assert result == {"condensed_query": "second rewrite"}


@pytest.mark.asyncio
async def test_reformulate_query_falls_back_to_original_condensed_query_on_failure():
    state = {
        "original_condensed_query": "original standalone question",
        "condensed_query": "first rewrite",
    }
    with patch(
        "app.graph.rag_graph.LLMClientService.resolve_platform_default",
        AsyncMock(side_effect=RuntimeError("no default configured")),
    ):
        result = await reformulate_query(state, _config())

    assert result == {}


@pytest.mark.asyncio
async def test_generate_windows_history_to_max_history_messages():
    captured = {}

    async def _capturing_stream(_resolved, *, system_prompt, messages):
        captured["messages"] = messages
        yield "ok"

    prior = [HumanMessage(content=f"turn-{i}") for i in range(MAX_HISTORY_MESSAGES + 5)]
    state = {
        "user_id": "u1",
        "thread_id": "t1",
        "stream_id": "s1",
        "query": "follow-up",
        "knowledge_type": "personal",
        "tenant_id": None,
        "knowledge_base_empty": False,
        "best_retrieved_chunks": [],
        "messages": [*prior, HumanMessage(content="follow-up")],
    }
    with (
        patch(
            "app.graph.rag_graph.LLMClientService.resolve_for_knowledge",
            AsyncMock(return_value="resolved-model"),
        ),
        patch(
            "app.graph.rag_graph.LLMClientService.stream_generate", _capturing_stream
        ),
    ):
        await generate(state, _config())

    sent = captured["messages"]
    # MAX_HISTORY_MESSAGES prior turns + the current query itself.
    assert len(sent) == MAX_HISTORY_MESSAGES + 1
    assert sent[0]["content"] == f"turn-{5}"  # oldest kept turn
    assert sent[-1]["content"] == "follow-up"
