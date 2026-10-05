"""_context_chunks and the no-context path it enables: Pinecone+Cohere
almost always return *something* from a non-empty namespace, so a
non-empty best_retrieved_chunks alone is not evidence the question is
answerable -- every chunk scoring below MIN_USABLE_RERANK_SCORE must
route generate() to the same "nothing relevant" prompt an empty list
would, and build_citations must agree with generate() on exactly which
chunks counted as context (see rag_graph.py's _context_chunks docstring)."""

from unittest.mock import AsyncMock, patch

import pytest

from app.graph.rag_graph import (
    MIN_USABLE_RERANK_SCORE,
    _context_chunks,
    build_citations,
    generate,
)


def _chunk(chunk_id: str, content: str, score: float) -> dict:
    return {
        "chunk_id": chunk_id,
        "document_id": "d1",
        "document_name": "doc.txt",
        "chunk_index": 0,
        "content": content,
        "score": score,
    }


def test_context_chunks_filters_out_everything_below_the_floor():
    state = {
        "best_retrieved_chunks": [
            _chunk("a", "irrelevant filler", MIN_USABLE_RERANK_SCORE - 0.01),
            _chunk("b", "also irrelevant", 0.0),
        ]
    }

    assert _context_chunks(state) == []


def test_context_chunks_keeps_chunks_at_or_above_the_floor_in_order():
    kept = [
        _chunk("a", "first", MIN_USABLE_RERANK_SCORE),
        _chunk("b", "second", 0.9),
    ]
    state = {"best_retrieved_chunks": kept}

    assert _context_chunks(state) == kept


@pytest.mark.asyncio
async def test_generate_uses_no_context_prompt_when_all_chunks_are_below_the_floor():
    captured = {}

    async def _capturing_stream(_resolved, *, system_prompt, messages):
        captured["system_prompt"] = system_prompt
        yield "ok"

    state = {
        "user_id": "u1",
        "thread_id": "t1",
        "stream_id": "s1",
        "query": "What's the revenue?",
        "knowledge_type": "personal",
        "tenant_id": None,
        "knowledge_base_empty": False,
        "best_retrieved_chunks": [
            _chunk("a", "irrelevant filler", MIN_USABLE_RERANK_SCORE - 0.01)
        ],
        "messages": [{"role": "user", "content": "What's the revenue?"}],
    }
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


@pytest.mark.asyncio
async def test_build_citations_is_empty_when_all_chunks_are_below_the_floor():
    state = {
        "best_retrieved_chunks": [
            _chunk("a", "irrelevant filler", MIN_USABLE_RERANK_SCORE - 0.01)
        ],
        # Even if the model somehow cited "[1]" anyway (it shouldn't, per
        # the no-context prompt's own rules), there is no context chunk
        # for it to resolve against once filtered.
        "response": "I don't have enough information [1].",
    }

    result = await build_citations(state, {})

    assert result["citations"] == []
