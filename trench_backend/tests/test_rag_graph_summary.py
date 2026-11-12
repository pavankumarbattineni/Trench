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


# --- generate() --------------------------------------------------------------


async def _generate_messages(prior: list, summary: str = "- The user weighs 60 kg."):
    captured = {}

    async def _capturing(_resolved, *, system_prompt, messages):
        captured["messages"] = messages
        yield "ok"

    state = {
        "user_id": "u1",
        "thread_id": "t1",
        "stream_id": "s1",
        "query": "and now?",
        "knowledge_type": "personal",
        "tenant_id": None,
        "knowledge_base_empty": False,
        "best_retrieved_chunks": [],
        "messages": [*prior, HumanMessage(content="and now?")],
        "conversation_summary": summary,
    }
    with (
        patch(
            "app.graph.rag_graph.LLMClientService.resolve_for_knowledge",
            AsyncMock(return_value="resolved-model"),
        ),
        patch("app.graph.rag_graph.LLMClientService.stream_generate", _capturing),
    ):
        await generate(state, _config())
    return captured["messages"]


@pytest.mark.asyncio
async def test_generate_skips_the_summary_while_the_whole_chat_fits_the_window():
    sent = await _generate_messages(_turns(MAX_HISTORY_MESSAGES // 2))
    assert not any("Summary of the earlier conversation" in m["content"] for m in sent)


@pytest.mark.asyncio
async def test_generate_puts_the_summary_ahead_of_the_window_once_messages_drop():
    sent = await _generate_messages(_turns(MAX_HISTORY_MESSAGES // 2 + 3))
    first = sent[0]
    assert first["role"] == "user"
    assert first["content"].startswith("Summary of the earlier conversation")
    assert "- The user weighs 60 kg." in first["content"]
    # Merged into the first windowed question, so roles still alternate.
    assert first["content"].endswith("question-4")
    roles = [m["role"] for m in sent]
    assert all(a != b for a, b in zip(roles, roles[1:], strict=False))
    assert len(sent) == MAX_HISTORY_MESSAGES + 1


@pytest.mark.asyncio
async def test_summary_becomes_its_own_message_when_the_window_starts_with_an_answer():
    prior = [AIMessage(content="answer-0"), *_turns(MAX_HISTORY_MESSAGES // 2)]
    sent = await _generate_messages([HumanMessage(content="question-0"), *prior])
    assert sent[0]["role"] == "user"
    assert sent[0]["content"].startswith("Summary of the earlier conversation")
    assert sent[1]["role"] == "assistant"
