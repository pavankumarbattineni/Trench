"""generate()'s choice of system prompt across the three distinct
situations it must tell apart: the scope has no documents at all
(knowledge_base_empty), the scope has documents but this turn's
retrieval found nothing relevant, and the normal case with real
context. Mixing up the second case with the third is what let the
model answer from outside knowledge and fabricate citation markers
with nothing behind them (see rag_graph.py's _NO_RELEVANT_CONTEXT_
SYSTEM_PROMPT)."""

from unittest.mock import AsyncMock, patch

import pytest

from app.graph.rag_graph import generate


def _state(**overrides) -> dict:
    base = {
        "user_id": "u1",
        "thread_id": "t1",
        "stream_id": "s1",
        "query": "What's the revenue?",
        "knowledge_type": "personal",
        "tenant_id": None,
        "knowledge_base_empty": False,
        "best_retrieved_chunks": [],
        "messages": [{"role": "user", "content": "What's the revenue?"}],
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_knowledge_base_empty_uses_the_empty_kb_prompt():
    captured = {}

    async def _capturing_stream(_resolved, *, system_prompt, messages):
        captured["system_prompt"] = system_prompt
        yield "ok"

    state = _state(knowledge_base_empty=True, best_retrieved_chunks=[])
    with (
        patch(
            "app.graph.rag_graph.LLMClientService.resolve_for_knowledge",
            AsyncMock(return_value="resolved-model"),
        ),
        patch(
            "app.graph.rag_graph.LLMClientService.stream_generate",
            _capturing_stream,
        ),
    ):
        await generate(state, {"configurable": {"db": object(), "user": object()}})

    assert "ZERO documents" in captured["system_prompt"]
    assert "upload a document" in captured["system_prompt"]


@pytest.mark.asyncio
async def test_empty_retrieval_uses_the_no_relevant_context_prompt():
    """The bug this guards against: the model must be told explicitly
    there's nothing to cite here, distinct from the knowledge_base_empty
    case -- otherwise it tends to answer from outside knowledge and
    invent citation markers with no chunk behind them."""
    captured = {}

    async def _capturing_stream(_resolved, *, system_prompt, messages):
        captured["system_prompt"] = system_prompt
        yield "ok"

    state = _state(knowledge_base_empty=False, best_retrieved_chunks=[])
    with (
        patch(
            "app.graph.rag_graph.LLMClientService.resolve_for_knowledge",
            AsyncMock(return_value="resolved-model"),
        ),
        patch(
            "app.graph.rag_graph.LLMClientService.stream_generate",
            _capturing_stream,
        ),
    ):
        await generate(state, {"configurable": {"db": object(), "user": object()}})

    prompt = captured["system_prompt"]
    assert "nothing relevant" in prompt
    assert "NEVER include citation markers" in prompt
    assert "zero documents" not in prompt


async def _generate_capturing(state: dict) -> dict:
    captured = {}

    async def _capturing_stream(_resolved, *, system_prompt, messages):
        captured["system_prompt"] = system_prompt
        captured["messages"] = messages
        yield "ok"

    with (
        patch(
            "app.graph.rag_graph.LLMClientService.resolve_for_knowledge",
            AsyncMock(return_value="resolved-model"),
        ),
        patch(
            "app.graph.rag_graph.LLMClientService.stream_generate",
            _capturing_stream,
        ),
    ):
        await generate(state, {"configurable": {"db": object(), "user": object()}})
    return captured


def _chunk(content: str, name: str = "notes.txt") -> dict:
    return {
        "chunk_id": "c1",
        "document_id": "d1",
        "document_name": name,
        "chunk_index": 0,
        "content": content,
        "score": 0.9,
    }


@pytest.mark.asyncio
async def test_normal_case_puts_numbered_context_in_the_last_user_message():
    captured = await _generate_capturing(
        _state(best_retrieved_chunks=[_chunk("Revenue grew 12%.")])
    )

    last = captured["messages"][-1]
    assert last["role"] == "user"
    assert "[1] (from notes.txt) Revenue grew 12%." in last["content"]
    # The question still comes last, after its context.
    assert last["content"].endswith("Question: What's the revenue?")
    assert "Revenue grew 12%." not in captured["system_prompt"]
    assert "NEVER include citation markers" not in captured["system_prompt"]


@pytest.mark.asyncio
async def test_normal_case_system_prompt_is_identical_whatever_was_retrieved():
    """The system prompt + history must be a stable prefix across turns so
    providers can cache it -- nothing per-turn may leak into it."""
    first = await _generate_capturing(
        _state(best_retrieved_chunks=[_chunk("Revenue grew 12%.")])
    )
    second = await _generate_capturing(
        _state(
            query="Draw a flowchart of the hiring process",
            best_retrieved_chunks=[_chunk("Step 1: apply.", "hiring.pdf")],
        )
    )
    assert first["system_prompt"] == second["system_prompt"]
    # The diagram reminder rides on the user message, not the system prompt.
    assert "Format reminder" in second["messages"][-1]["content"]
