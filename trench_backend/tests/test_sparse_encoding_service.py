"""Sparse (keyword) encoding via Pinecone's hosted pinecone-sparse-english-v0:
correct input_type per side (passage vs query), batches of at most 96
inputs, order preserved, and Pinecone's response mapped to the
{"indices", "values"} shape the vector store expects."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.service import sparse_encoding_service
from app.service.sparse_encoding_service import SparseEncodingService


class _FakeInference:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def embed(self, *, model, inputs, parameters):
        self.calls.append(
            {"model": model, "inputs": list(inputs), "parameters": parameters}
        )
        # Encode each input's position into its index so order is checkable.
        return SimpleNamespace(
            data=[
                SimpleNamespace(sparse_indices=[len(text)], sparse_values=[0.5])
                for text in inputs
            ]
        )


@pytest.fixture
def fake_inference():
    inference = _FakeInference()
    client = SimpleNamespace(inference=inference)
    with patch.object(sparse_encoding_service, "_client", lambda: client):
        yield inference


@pytest.mark.asyncio
async def test_documents_use_passage_input_type_and_hosted_model(fake_inference):
    vectors = await SparseEncodingService.encode_documents(["abc", "de"])

    [call] = fake_inference.calls
    assert call["model"] == "pinecone-sparse-english-v0"
    assert call["parameters"]["input_type"] == "passage"
    assert call["parameters"]["max_tokens_per_sequence"] == 2048
    assert vectors == [
        {"indices": [3], "values": [0.5]},
        {"indices": [2], "values": [0.5]},
    ]


@pytest.mark.asyncio
async def test_query_uses_query_input_type(fake_inference):
    vector = await SparseEncodingService.encode_query("what is attention")

    [call] = fake_inference.calls
    assert call["parameters"]["input_type"] == "query"
    assert call["inputs"] == ["what is attention"]
    assert vector == {"indices": [17], "values": [0.5]}


@pytest.mark.asyncio
async def test_documents_are_sent_in_batches_of_at_most_96(fake_inference):
    texts = ["x" * (i + 1) for i in range(200)]
    vectors = await SparseEncodingService.encode_documents(texts)

    assert [len(c["inputs"]) for c in fake_inference.calls] == [96, 96, 8]
    # Order preserved across batches.
    assert [v["indices"][0] for v in vectors] == [len(t) for t in texts]


@pytest.mark.asyncio
async def test_empty_input_makes_no_api_call(fake_inference):
    assert await SparseEncodingService.encode_documents([]) == []
    assert fake_inference.calls == []
