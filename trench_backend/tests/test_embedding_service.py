"""L2 normalization: gemini-embedding-001 only returns unit-length vectors
at its native 3072 dimensions -- at the truncated output_dimensionality
(768) this app actually uses, the API returns raw, unnormalized values, so
every vector embed_texts/embed_query returns must be normalized here
manually (see embedding_service.py's module docstring)."""

import math
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.service.embedding_service import EmbeddingService, _l2_normalize


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
