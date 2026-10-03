import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.graph.rag_graph import hybrid_retrieve
from app.service.retrieval_service import RetrievedChunk


def _retrieved_chunk(chunk_id: str, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="d1",
        document_name="doc.txt",
        chunk_index=0,
        content=f"content for {chunk_id}",
        score=score,
    )


@pytest.mark.asyncio
async def test_hybrid_retrieve_reranks_results_via_reranker_service():
    state = {
        "user_id": str(uuid.uuid4()),
        "knowledge_type": "personal",
        "tenant_id": None,
        "condensed_query": "my query",
    }
    raw_chunks = [_retrieved_chunk("low", 0.3), _retrieved_chunk("high", 0.9)]

    with (
        patch(
            "app.graph.rag_graph.RetrievalService.retrieve",
            new=AsyncMock(return_value=raw_chunks),
        ),
        patch(
            "app.graph.rag_graph.RerankerService.rerank",
            new=AsyncMock(
                return_value=[
                    {"chunk_id": "high", "content": "content for high"},
                    {"chunk_id": "low", "content": "content for low"},
                ]
            ),
        ) as mock_rerank,
    ):
        result = await hybrid_retrieve(state, {})

    assert [c["chunk_id"] for c in result["retrieved_chunks"]] == ["high", "low"]
    mock_rerank.assert_awaited_once()
    assert mock_rerank.await_args.args[0] == "my query"


@pytest.mark.asyncio
async def test_hybrid_retrieve_requests_more_candidates_than_it_keeps():
    """Retrieves a wider candidate pool from Pinecone than the final
    reranked count, so the reranker has something meaningful to narrow
    down (architecture doc §3: "the top ~20-50 hybrid results")."""
    state = {
        "user_id": str(uuid.uuid4()),
        "knowledge_type": "personal",
        "tenant_id": None,
        "condensed_query": "my query",
    }

    with (
        patch(
            "app.graph.rag_graph.RetrievalService.retrieve",
            new=AsyncMock(return_value=[]),
        ) as mock_retrieve,
        patch(
            "app.graph.rag_graph.RerankerService.rerank",
            new=AsyncMock(return_value=[]),
        ),
    ):
        await hybrid_retrieve(state, {})

    assert mock_retrieve.await_args.kwargs["top_k"] > 8
