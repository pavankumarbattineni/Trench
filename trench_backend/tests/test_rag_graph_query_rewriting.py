"""condense_query and reformulate_query: both use the platform default
model (never the user's selected model/BYOK credential -- these are cheap
internal rewrite steps, see LLMClientService.resolve_platform_default),
reformulate_query always rewrites original_condensed_query rather than
compounding onto a previous reformulation. condense_query sees only the
last few questions and the start of the last answer; generate gets
MAX_HISTORY_MESSAGES of history with interactive JSON reduced to hints."""

from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.graph.rag_graph import (
    MAX_HISTORY_MESSAGES,
    condense_query,
    generate,
    reformulate_query,
    strip_interactive_blocks,
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


_CALCULATOR_ANSWER = (
    "Your targets are below [1].\n"
    "```trench-ui\n"
    '{"version": 1, "components": [{"type": "calculator", "title": "BMR", '
    '"inputs": [{"id": "w", "label": "Weight", "default": 60}], '
    '"outputs": [{"id": "b", "label": "BMR", "formula": "10*w"}]}]}\n'
    "```\n"
    "Adjust them as needed."
)


async def _condense_prompt(messages: list) -> str:
    captured = {}

    async def _fake_stream(_resolved, *, system_prompt, messages):
        captured["prompt"] = messages[0]["content"]
        yield "rewritten"

    with (
        patch(
            "app.graph.rag_graph.LLMClientService.resolve_platform_default",
            AsyncMock(return_value="resolved-model"),
        ),
        patch("app.graph.rag_graph.LLMClientService.stream_generate", _fake_stream),
    ):
        await condense_query(
            {"query": messages[-1].content, "messages": messages}, _config()
        )
    return captured["prompt"]


@pytest.mark.asyncio
async def test_condense_query_sends_only_the_last_questions_and_answer_start():
    """Uses the LangChain message objects the checkpointer restores into
    state["messages"]. Only the last CONDENSE_QUESTION_COUNT questions and
    the start of the last answer are sent -- never whole answers."""
    prior = []
    for i in range(5):
        prior += [
            HumanMessage(content=f"question-{i}"),
            AIMessage(content=f"answer-{i} " + "detail " * 400),
        ]
    prompt = await _condense_prompt([*prior, HumanMessage(content="the second one?")])

    assert "question-0" not in prompt and "question-1" not in prompt
    assert all(f"question-{i}" in prompt for i in (2, 3, 4))
    assert "answer-4" in prompt  # the last answer's start
    assert "answer-3" not in prompt  # earlier answers aren't sent at all
    assert len(prompt) < 2000  # bounded however long the answers are
    assert prompt.endswith("Follow-up: the second one?")


@pytest.mark.asyncio
async def test_condense_query_reduces_interactive_json_to_a_hint():
    prompt = await _condense_prompt(
        [
            HumanMessage(content="Work out my BMR"),
            AIMessage(content=_CALCULATOR_ANSWER),
            HumanMessage(content="change the weight to 65"),
        ]
    )
    assert '"formula"' not in prompt
    assert '[Interactive view shown to the user: calculator "BMR"' in prompt


@pytest.mark.asyncio
async def test_condense_query_falls_back_to_raw_query_when_the_call_fails():
    """A provider error mid-rewrite (e.g. Groq 413 'Request too large ...
    tokens per minute') must not fail the turn: search with the raw query."""

    async def _failing_stream(_resolved, *, system_prompt, messages):
        raise RuntimeError("Error code: 413 - Request too large (TPM)")
        yield  # pragma: no cover -- makes this an async generator

    state = {
        "query": "Which food has the most protein?",
        "messages": [
            HumanMessage(content="Plan my meals"),
            AIMessage(content="Here is a plan."),
            HumanMessage(content="Which food has the most protein?"),
        ],
    }
    with (
        patch(
            "app.graph.rag_graph.LLMClientService.resolve_platform_default",
            AsyncMock(return_value="resolved-model"),
        ),
        patch("app.graph.rag_graph.LLMClientService.stream_generate", _failing_stream),
    ):
        result = await condense_query(state, _config())

    assert result == {
        "condensed_query": "Which food has the most protein?",
        "original_condensed_query": "Which food has the most protein?",
    }


def test_strip_interactive_blocks_keeps_text_and_ordinary_json():
    stripped = strip_interactive_blocks(_CALCULATOR_ANSWER)
    assert stripped.startswith("Your targets are below [1].")
    assert stripped.endswith("Adjust them as needed.")
    assert "```" not in stripped and '"inputs"' not in stripped
    assert 'calculator "BMR" (inputs: Weight; results: BMR)' in stripped

    plain = 'An example:\n```json\n{"name": "x"}\n```'
    assert strip_interactive_blocks(plain) == plain

    # An answer interrupted inside the block still loses the partial JSON.
    partial = 'Here [1]:\n```trench-ui\n{"components": [{"type": "bar_ch'
    assert strip_interactive_blocks(partial) == (
        "Here [1]:\n[Interactive view shown to the user]"
    )


@pytest.mark.asyncio
async def test_generate_history_has_interactive_json_replaced_by_hints():
    captured = {}

    async def _capturing_stream(_resolved, *, system_prompt, messages):
        captured["messages"] = messages
        yield "ok"

    state = {
        "user_id": "u1",
        "thread_id": "t1",
        "stream_id": "s1",
        "query": "make it 65 kg",
        "knowledge_type": "personal",
        "tenant_id": None,
        "knowledge_base_empty": False,
        "best_retrieved_chunks": [],
        "messages": [
            HumanMessage(content="Work out my BMR"),
            AIMessage(content=_CALCULATOR_ANSWER),
            HumanMessage(content="make it 65 kg"),
        ],
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

    past_answer = captured["messages"][1]
    assert past_answer["role"] == "assistant"
    assert "```trench-ui" not in past_answer["content"]
    assert "Interactive view shown to the user" in past_answer["content"]
    assert captured["messages"][0]["content"] == "Work out my BMR"


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
