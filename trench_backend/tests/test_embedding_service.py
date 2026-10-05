"""L2 normalization: gemini-embedding-001 only returns unit-length vectors
at its native 3072 dimensions -- at the truncated output_dimensionality
(768) this app actually uses, the API returns raw, unnormalized values, so
every vector embed_texts/embed_query returns must be normalized here
manually (see embedding_service.py's module docstring). Also batching:
embed_texts must split a request over _EMBED_BATCH_SIZE texts into
multiple API calls rather than sending them all in one `contents` list,
which Google's API rejects past 100 items."""

import math
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.service.embedding_service import (
    _EMBED_BATCH_SIZE,
    EmbeddingService,
    _l2_normalize,
)


def _norm(vector: list[float]) -> float:
    return math.sqrt(sum(v * v for v in vector))


def test_l2_normalize_produces_a_unit_vector():
    result = _l2_normalize([3.0, 4.0])
    assert abs(_norm(result) - 1.0) < 1e-9


def test_l2_normalize_zero_vector_does_not_crash():
    result = _l2_normalize([0.0, 0.0, 0.0])
    assert result == [0.0, 0.0, 0.0]


def _fake_response(vectors: list[list[float]]):
    return SimpleNamespace(embeddings=[SimpleNamespace(values=v) for v in vectors])


@pytest.mark.asyncio
async def test_embed_texts_returns_normalized_vectors():
    fake_client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(
                embed_content=AsyncMock(
                    return_value=_fake_response([[3.0, 4.0], [1.0, 0.0]])
                )
            )
        )
    )
    with patch("app.service.embedding_service._client", return_value=fake_client):
        result = await EmbeddingService.embed_texts(["a", "b"])

    for vector in result:
        assert abs(_norm(vector) - 1.0) < 1e-6


@pytest.mark.asyncio
async def test_embed_query_returns_a_normalized_vector():
    fake_client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(
                embed_content=AsyncMock(return_value=_fake_response([[6.0, 8.0]]))
            )
        )
    )
    with patch("app.service.embedding_service._client", return_value=fake_client):
        result = await EmbeddingService.embed_query("hello")

    assert abs(_norm(result) - 1.0) < 1e-6


@pytest.mark.asyncio
async def test_embed_texts_with_empty_input_returns_empty_without_calling_the_api():
    with patch("app.service.embedding_service._client") as mock_client:
        result = await EmbeddingService.embed_texts([])

    assert result == []
    mock_client.assert_not_called()


@pytest.mark.asyncio
async def test_embed_texts_at_exactly_the_batch_size_makes_one_call():
    texts = [f"chunk-{i}" for i in range(_EMBED_BATCH_SIZE)]
    embed_content = AsyncMock(return_value=_fake_response([[1.0, 0.0] for _ in texts]))
    fake_client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(embed_content=embed_content))
    )
    with patch("app.service.embedding_service._client", return_value=fake_client):
        result = await EmbeddingService.embed_texts(texts)

    embed_content.assert_awaited_once()
    assert len(result) == _EMBED_BATCH_SIZE


@pytest.mark.asyncio
async def test_embed_texts_over_the_batch_size_splits_into_multiple_calls():
    texts = [f"chunk-{i}" for i in range(_EMBED_BATCH_SIZE + 5)]
    call_sizes: list[int] = []

    async def _fake_embed_content(*, model, contents, config):
        call_sizes.append(len(contents))
        return _fake_response([[1.0, 0.0] for _ in contents])

    fake_client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(embed_content=_fake_embed_content))
    )
    with patch("app.service.embedding_service._client", return_value=fake_client):
        result = await EmbeddingService.embed_texts(texts)

    assert call_sizes == [_EMBED_BATCH_SIZE, 5]
    assert len(result) == len(texts)


@pytest.mark.asyncio
async def test_embed_texts_preserves_order_across_batches():
    texts = [f"chunk-{i}" for i in range(_EMBED_BATCH_SIZE + 2)]
    # A distinct, non-parallel raw vector per input index -- normalization
    # preserves direction, so each result vector's index is still
    # recoverable after it, unlike a magnitude-only encoding would be.
    raw_vectors = {text: [float(i + 1), 1.0] for i, text in enumerate(texts)}

    async def _fake_embed_content(*, model, contents, config):
        return _fake_response([raw_vectors[text] for text in contents])

    fake_client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(embed_content=_fake_embed_content))
    )
    with patch("app.service.embedding_service._client", return_value=fake_client):
        result = await EmbeddingService.embed_texts(texts)

    expected = [_l2_normalize(raw_vectors[text]) for text in texts]
    for actual_vector, expected_vector in zip(result, expected, strict=True):
        assert actual_vector == pytest.approx(expected_vector)
