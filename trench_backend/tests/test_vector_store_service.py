"""PineconeVectorStore.delete_by_prefix and delete_namespace -- the prefix
delete has no server-side equivalent in Pinecone, so it's list-then-delete
(paginated), and must actually delete every page's batch rather than only
the first -- and scale_hybrid, the dense/sparse weighting applied
identically to documents (upsert) and queries (query)."""

from unittest.mock import MagicMock

import pytest

from app.service.vector_store_service import (
    HYBRID_DENSE_WEIGHT,
    PineconeVectorStore,
    VectorRecord,
    scale_hybrid,
)


class _FakeIndex:
    def __init__(self, pages: list[list[str]] | None = None) -> None:
        self._pages = pages or []
        self.delete_calls: list[dict] = []
        self.upsert_calls: list[dict] = []
        self.query_calls: list[dict] = []

    def list(self, *, prefix, limit, namespace):
        return iter(self._pages)

    def delete(self, **kwargs):
        self.delete_calls.append(kwargs)

    def upsert(self, **kwargs):
        self.upsert_calls.append(kwargs)

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        return {"matches": []}


def _store_with_fake_index(fake_index: _FakeIndex) -> PineconeVectorStore:
    store = PineconeVectorStore(api_key="k", index_name="idx", dimensions=768)
    store._ensure_index = MagicMock()
    store._client = MagicMock()
    store._client.Index.return_value = fake_index
    return store


def test_scale_hybrid_scales_dense_by_alpha_and_sparse_values_by_one_minus_alpha():
    dense = [1.0, 2.0, 3.0]
    sparse = {"indices": [5, 9], "values": [1.0, 2.0]}

    scaled_dense, scaled_sparse = scale_hybrid(dense, sparse, alpha=0.7)

    assert scaled_dense == pytest.approx([0.7, 1.4, 2.1])
    assert scaled_sparse["indices"] == [5, 9]
    assert scaled_sparse["values"] == pytest.approx([0.3, 0.6])


def test_scale_hybrid_preserves_sparse_indices_unchanged():
    sparse = {"indices": [1, 2, 3], "values": [1.0, 1.0, 1.0]}

    _, scaled_sparse = scale_hybrid([1.0], sparse, alpha=0.5)

    assert scaled_sparse["indices"] is sparse["indices"]


def test_scale_hybrid_defaults_to_hybrid_dense_weight():
    dense = [2.0]
    sparse = {"indices": [0], "values": [2.0]}

    scaled_dense, scaled_sparse = scale_hybrid(dense, sparse)

    assert scaled_dense == [2.0 * HYBRID_DENSE_WEIGHT]
    assert scaled_sparse["values"] == [2.0 * (1 - HYBRID_DENSE_WEIGHT)]


@pytest.mark.asyncio
async def test_upsert_scales_each_records_vectors():
    fake_index = _FakeIndex()
    store = _store_with_fake_index(fake_index)
    record = VectorRecord(
        chunk_id="c1",
        dense_vector=[1.0, 0.0],
        sparse_vector={"indices": [0], "values": [1.0]},
        metadata={},
    )

    await store.upsert(namespace="ns", records=[record])

    sent = fake_index.upsert_calls[0]["vectors"][0]
    assert sent["values"] == [HYBRID_DENSE_WEIGHT, 0.0]
    assert sent["sparse_values"]["values"] == [1 - HYBRID_DENSE_WEIGHT]


@pytest.mark.asyncio
async def test_query_scales_the_query_vectors_the_same_way_as_upsert():
    fake_index = _FakeIndex()
    store = _store_with_fake_index(fake_index)

    await store.query(
        namespace="ns",
        dense_vector=[1.0, 0.0],
        sparse_vector={"indices": [0], "values": [1.0]},
        top_k=5,
    )

    sent = fake_index.query_calls[0]
    assert sent["vector"] == [HYBRID_DENSE_WEIGHT, 0.0]
    assert sent["sparse_vector"]["values"] == [1 - HYBRID_DENSE_WEIGHT]


@pytest.mark.asyncio
async def test_delete_by_prefix_deletes_every_page():
    fake_index = _FakeIndex(pages=[["doc:0", "doc:1"], ["doc:2"]])
    store = _store_with_fake_index(fake_index)

    await store.delete_by_prefix(namespace="personal:u1", prefix="doc:")

    assert fake_index.delete_calls == [
        {"ids": ["doc:0", "doc:1"], "namespace": "personal:u1"},
        {"ids": ["doc:2"], "namespace": "personal:u1"},
    ]


@pytest.mark.asyncio
async def test_delete_by_prefix_skips_empty_pages():
    fake_index = _FakeIndex(pages=[[]])
    store = _store_with_fake_index(fake_index)

    await store.delete_by_prefix(namespace="personal:u1", prefix="doc:")

    assert fake_index.delete_calls == []


@pytest.mark.asyncio
async def test_delete_namespace_uses_delete_all():
    fake_index = _FakeIndex(pages=[])
    store = _store_with_fake_index(fake_index)

    await store.delete_namespace(namespace="personal:u1")

    assert fake_index.delete_calls == [{"delete_all": True, "namespace": "personal:u1"}]
