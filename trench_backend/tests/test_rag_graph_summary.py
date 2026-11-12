"""summarize_history (rolling conversation memory every SUMMARY_EVERY_TURNS
exchanges) and how generate() uses it once messages leave the window."""

from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.graph.rag_graph import (
    MAX_HISTORY_MESSAGES,
    SUMMARY_EVERY_TURNS,
    build_graph,
    generate,
    summarize_history,
)


def _config():
    return {"configurable": {"db": object(), "user": object()}}


def _turns(n: int, start: int = 1) -> list:
    messages = []
    for i in range(start, start + n):
        messages += [
            HumanMessage(content=f"question-{i}"),
            AIMessage(content=f"answer-{i}"),
        ]
    return messages


async def _run_summary(state: dict, reply: str = "- The user weighs 60 kg."):
    captured = {"calls": 0}

    async def _fake_stream(_resolved, *, system_prompt, messages):
        captured["calls"] += 1
        captured["system"] = system_prompt
        captured["prompt"] = messages[0]["content"]
        yield reply

    with (
        patch(
            "app.graph.rag_graph.LLMClientService.resolve_platform_default",
            AsyncMock(return_value="resolved-model"),
        ),
        patch("app.graph.rag_graph.LLMClientService.stream_generate", _fake_stream),
    ):
        result = await summarize_history(state, _config())
    return result, captured


@pytest.mark.asyncio
async def test_no_summary_before_the_fifth_exchange():
    result, captured = await _run_summary({"messages": _turns(SUMMARY_EVERY_TURNS - 1)})
    assert result == {}
    assert captured["calls"] == 0


@pytest.mark.asyncio
async def test_first_summary_after_the_fifth_exchange():
    messages = _turns(SUMMARY_EVERY_TURNS)
    result, captured = await _run_summary({"messages": messages})

    assert result == {
        "conversation_summary": "- The user weighs 60 kg.",
        "summary_message_count": len(messages),
    }
    prompt = captured["prompt"]
    assert "(none yet)" in prompt  # no previous memory
    assert all(f"question-{i}" in prompt for i in range(1, 6))
    assert "Keep every specific value exactly as stated" in prompt


@pytest.mark.asyncio
async def test_not_due_again_until_five_more_exchanges():
    state = {
        "messages": _turns(SUMMARY_EVERY_TURNS + 4),
        "conversation_summary": "old memory",
        "summary_message_count": 2 * SUMMARY_EVERY_TURNS,
    }
    result, captured = await _run_summary(state)
    assert result == {}
    assert captured["calls"] == 0


@pytest.mark.asyncio
async def test_tenth_exchange_folds_only_the_new_turns_into_the_old_memory():
    messages = _turns(2 * SUMMARY_EVERY_TURNS)
    state = {
        "messages": messages,
        "conversation_summary": "- The user has a home loan.",
        "summary_message_count": 2 * SUMMARY_EVERY_TURNS,  # turns 1-5 covered
    }
    result, captured = await _run_summary(state, reply="- Updated memory.")

    prompt = captured["prompt"]
    assert "- The user has a home loan." in prompt
    assert "question-6" in prompt and "question-10" in prompt
    assert "question-1\n" not in prompt and "answer-5" not in prompt
    assert result["summary_message_count"] == len(messages)
    assert result["conversation_summary"] == "- Updated memory."


@pytest.mark.asyncio
async def test_a_missed_update_is_made_on_the_next_turn():
    """Summary last covered 5 exchanges, but the 10th's update was missed:
    the 11th exchange catches up."""
    state = {
        "messages": _turns(2 * SUMMARY_EVERY_TURNS + 1),
        "conversation_summary": "old",
        "summary_message_count": 2 * SUMMARY_EVERY_TURNS,
    }
    result, captured = await _run_summary(state)
    assert captured["calls"] == 1
    assert "question-11" in captured["prompt"]
    assert result["summary_message_count"] == len(state["messages"])


@pytest.mark.asyncio
async def test_interactive_json_and_long_answers_are_trimmed_for_the_summarizer():
    big_answer = (
        "Here is your calculator [1].\n```trench-ui\n"
        '{"version": 1, "components": [{"type": "calculator", "title": "BMR", '
        '"inputs": [{"id": "w", "label": "Weight", "default": 60}], '
        '"outputs": [{"id": "b", "label": "BMR", "formula": "10*w"}]}]}\n```\n'
        + "More detail. "
        * 500
    )
    messages = _turns(SUMMARY_EVERY_TURNS - 1) + [
        HumanMessage(content="Work out my BMR"),
        AIMessage(content=big_answer),
    ]
    _, captured = await _run_summary({"messages": messages})
    prompt = captured["prompt"]
    assert '"formula"' not in prompt
    assert 'calculator "BMR"' in prompt
    assert len(prompt) < 12000  # 5 turns stay a few thousand tokens


@pytest.mark.asyncio
async def test_summary_failure_keeps_the_previous_memory():
    async def _failing(_resolved, *, system_prompt, messages):
        raise RuntimeError("Error code: 413 - Request too large")
        yield  # pragma: no cover

    with (
        patch(
            "app.graph.rag_graph.LLMClientService.resolve_platform_default",
            AsyncMock(return_value="resolved-model"),
        ),
        patch("app.graph.rag_graph.LLMClientService.stream_generate", _failing),
    ):
        result = await summarize_history(
            {"messages": _turns(SUMMARY_EVERY_TURNS)}, _config()
        )
    assert result == {}


@pytest.mark.asyncio
async def test_no_summary_for_an_access_denied_turn():
    result, captured = await _run_summary(
        {"messages": _turns(SUMMARY_EVERY_TURNS), "access_denied": True}
    )
    assert result == {} and captured["calls"] == 0


def test_summarize_history_runs_after_persist():
    graph = build_graph()
    assert "summarize_history" in graph.nodes
    edges = {(e[0], e[1]) for e in graph.edges}
    assert ("persist", "summarize_history") in edges


# --- generate(): summary + only the exchanges it doesn't cover ----------------


async def _sent(prior: list, *, covered: int, summary: str = "- MEMORY") -> list:
    captured = {}

    async def _capturing(_resolved, *, system_prompt, messages):
        captured["messages"] = messages
        yield "ok"

    state = {
        "user_id": "u1",
        "thread_id": "t1",
        "stream_id": "s1",
        "query": "next?",
        "knowledge_type": "personal",
        "tenant_id": None,
        "knowledge_base_empty": False,
        "best_retrieved_chunks": [],
        "messages": [*prior, HumanMessage(content="next?")],
        "conversation_summary": summary,
        "summary_message_count": covered,
    }
    with (
        patch(
            "app.graph.rag_graph.LLMClientService.resolve_for_knowledge",
            AsyncMock(return_value="resolved-model"),
        ),
        patch("app.graph.rag_graph.LLMClientService.stream_generate", _capturing),
    ):
        await generate(state, _config())
    return captured["messages"][:-1]  # history only, without the current question


def _contents(history: list) -> str:
    return "\n".join(m["content"] for m in history)


@pytest.mark.asyncio
async def test_without_a_summary_the_last_ten_messages_are_sent():
    history = await _sent(_turns(7), covered=0, summary="")
    assert len(history) == MAX_HISTORY_MESSAGES
    assert "Summary of the earlier conversation" not in _contents(history)
    assert history[0]["content"] == "question-3"


@pytest.mark.asyncio
async def test_sixth_question_gets_the_summary_plus_only_the_fifth_exchange():
    """Exchanges 1-5 are summarized; only the latest one is resent in full,
    so "the second one" style follow-ups still have the exact last answer."""
    history = await _sent(_turns(5), covered=10)
    text = _contents(history)
    assert history[0]["content"].startswith("Summary of the earlier conversation")
    assert "- MEMORY" in text
    assert "question-5" in text and "answer-5" in text
    assert "question-4" not in text and "answer-1" not in text
    assert [m["role"] for m in history] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_seventh_question_gets_the_summary_plus_the_sixth_exchange():
    history = await _sent(_turns(6), covered=10)
    text = _contents(history)
    assert "question-6" in text and "answer-6" in text
    assert "question-5" not in text and "answer-5" not in text


@pytest.mark.asyncio
async def test_tenth_question_gets_the_summary_plus_exchanges_six_to_nine():
    history = await _sent(_turns(9), covered=10)
    text = _contents(history)
    assert all(f"question-{i}" in text for i in range(6, 10))
    assert "question-5" not in text
    assert len(history) == 8
    roles = [m["role"] for m in history]
    assert all(a != b for a, b in zip(roles, roles[1:], strict=False))


@pytest.mark.asyncio
async def test_eleventh_question_after_the_update_gets_only_the_tenth_exchange():
    history = await _sent(_turns(10), covered=20)
    text = _contents(history)
    assert "question-10" in text and "answer-10" in text
    assert "question-9" not in text


@pytest.mark.asyncio
async def test_a_stale_summary_still_caps_the_history_at_ten_messages():
    """If summary updates keep failing, the uncovered messages are still
    capped, so the request can't grow without bound."""
    history = await _sent(_turns(12), covered=10)
    assert len(history) == MAX_HISTORY_MESSAGES
    assert history[0]["content"].startswith("Summary of the earlier conversation")
    assert "question-12" in _contents(history)
