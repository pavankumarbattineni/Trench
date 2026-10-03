from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.service.reranker_service import RerankerService


def _chunk(chunk_id: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "document_id": "d1",
        "document_name": "doc.txt",
        "chunk_index": 0,
        "content": f"content for {chunk_id}",
        "score": 0.5,
    }


def _cohere_response(*, result_indexes: list[int]) -> SimpleNamespace:
    """Mimics cohere.v2.types.V2RerankResponse's shape closely enough for
    RerankerService to consume -- Cohere's own response is already sorted
    by relevance_score descending, which is what `result_indexes` encodes
    here (the original-list indexes in reranked order)."""
    return SimpleNamespace(
        results=[
            SimpleNamespace(index=i, relevance_score=1.0 - position * 0.1)
            for position, i in enumerate(result_indexes)
        ]
    )


@pytest.mark.asyncio
async def test_rerank_reorders_chunks_per_cohere_result_order():
    chunks = [_chunk("low"), _chunk("high"), _chunk("mid")]
    # Cohere says: original index 1 ("high") is most relevant, then 2
    # ("mid"), then 0 ("low").
    fake_client = AsyncMock()
    fake_client.rerank = AsyncMock(
        return_value=_cohere_response(result_indexes=[1, 2, 0])
    )

    with patch("app.service.reranker_service._client", return_value=fake_client):
        result = await RerankerService.rerank("some query", chunks)

    assert [c["chunk_id"] for c in result] == ["high", "mid", "low"]


@pytest.mark.asyncio
async def test_rerank_passes_query_and_document_contents_to_cohere():
    chunks = [_chunk("a"), _chunk("b")]
    fake_client = AsyncMock()
    fake_client.rerank = AsyncMock(return_value=_cohere_response(result_indexes=[0, 1]))

    with patch("app.service.reranker_service._client", return_value=fake_client):
        await RerankerService.rerank("my query", chunks, top_k=5)

    fake_client.rerank.assert_awaited_once()
    call_kwargs = fake_client.rerank.await_args.kwargs
    assert call_kwargs["query"] == "my query"
    assert call_kwargs["documents"] == ["content for a", "content for b"]
    assert call_kwargs["top_n"] == 5


@pytest.mark.asyncio
async def test_rerank_returns_empty_list_for_no_chunks_without_calling_cohere():
    fake_client = AsyncMock()

    with patch("app.service.reranker_service._client", return_value=fake_client):
        result = await RerankerService.rerank("some query", [])

    assert result == []
    fake_client.rerank.assert_not_called()
