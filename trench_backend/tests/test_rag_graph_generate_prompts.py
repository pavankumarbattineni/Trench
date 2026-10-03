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
        "retrieved_chunks": [],
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

    state = _state(knowledge_base_empty=True, retrieved_chunks=[])
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

    state = _state(knowledge_base_empty=False, retrieved_chunks=[])
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


@pytest.mark.asyncio
async def test_normal_case_with_chunks_builds_numbered_context():
    captured = {}

    async def _capturing_stream(_resolved, *, system_prompt, messages):
        captured["system_prompt"] = system_prompt
        yield "ok"

    state = _state(
        knowledge_base_empty=False,
        retrieved_chunks=[
            {
                "chunk_id": "c1",
                "document_id": "d1",
                "document_name": "notes.txt",
                "chunk_index": 0,
                "content": "Revenue grew 12%.",
                "score": 0.9,
            }
        ],
    )
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
    assert "[1] (from notes.txt) Revenue grew 12%." in prompt
    assert "NEVER include citation markers" not in prompt
