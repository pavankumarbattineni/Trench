"""Dense+sparse (hybrid) vector storage/retrieval -- Pinecone-backed, one
operator-owned index shared by all free-tier users and tenants,
partitioned into per-scope namespaces so company and personal knowledge
(and different tenants' company knowledge) never mix in the same
retrieval scope.

There is no Postgres document_chunks table: each chunk's text lives only
as Pinecone metadata alongside its dense+sparse vectors, so a query
returns ready-to-use context/citation text directly -- no join-back to
Postgres needed. Postgres itself never stores embeddings, per the
project's standing "embeddings only in the vector database" rule.
"""

import asyncio
import uuid
from functools import lru_cache
from typing import Protocol

from pinecone import Pinecone, ServerlessSpec

from app.config import get_settings
from app.service.sparse_encoding_service import SparseVector


def personal_namespace(user_id: uuid.UUID) -> str:
    return f"personal:{user_id}"


def company_namespace(tenant_id: uuid.UUID) -> str:
    return f"company:{tenant_id}"


# How much weight the dense (semantic) vector gets relative to the sparse
# (BM25 keyword) vector in Pinecone's combined dotproduct score -- applied
# identically to documents at upsert time and to queries at query time (see
# scale_hybrid), since hybrid search only makes sense when both sides were
# scaled the same way.
HYBRID_DENSE_WEIGHT = 0.7


def scale_hybrid(
    dense: list[float], sparse: SparseVector, alpha: float = HYBRID_DENSE_WEIGHT
) -> tuple[list[float], SparseVector]:
    """Scales a dense vector by `alpha` and a sparse vector's values by
    `1 - alpha`, leaving the sparse vector's indices untouched -- the
    standard convex-combination technique for weighting Pinecone's hybrid
    dotproduct score toward dense or sparse retrieval."""
    scaled_dense = [value * alpha for value in dense]
    scaled_sparse: SparseVector = {
        "indices": sparse["indices"],
        "values": [value * (1 - alpha) for value in sparse["values"]],
    }
    return scaled_dense, scaled_sparse


class VectorRecord:
    def __init__(
        self,
        *,
        chunk_id: str,
        dense_vector: list[float],
        sparse_vector: SparseVector,
        metadata: dict,
    ) -> None:
        self.chunk_id = chunk_id
        self.dense_vector = dense_vector
        self.sparse_vector = sparse_vector
        self.metadata = metadata


class ScoredChunk:
    def __init__(self, *, chunk_id: str, score: float, metadata: dict) -> None:
        self.chunk_id = chunk_id
        self.score = score
        self.metadata = metadata


class VectorStoreProvider(Protocol):
    async def upsert(self, *, namespace: str, records: list[VectorRecord]) -> None: ...

    async def query(
        self,
        *,
        namespace: str,
        dense_vector: list[float],
        sparse_vector: SparseVector,
        top_k: int,
    ) -> list[ScoredChunk]: ...

    async def delete(self, *, namespace: str, ids: list[str]) -> None: ...

    async def delete_by_prefix(self, *, namespace: str, prefix: str) -> None: ...

    async def delete_namespace(self, *, namespace: str) -> None: ...


class PineconeVectorStore:
    """Thin async-friendly wrapper -- the official `pinecone` client is
    synchronous, so calls are offloaded to a thread like local disk I/O
    elsewhere in this app.

    The index uses the "dotproduct" metric, required by Pinecone for
    combined dense+sparse (hybrid) scoring in a single query.
    """

    def __init__(self, api_key: str, index_name: str, dimensions: int) -> None:
        self._client = Pinecone(api_key=api_key)
        self._index_name = index_name
        self._dimensions = dimensions

    def _ensure_index(self) -> None:
        existing = {index["name"] for index in self._client.list_indexes()}
        if self._index_name not in existing:
            self._client.create_index(
                name=self._index_name,
                dimension=self._dimensions,
                metric="dotproduct",
                spec=ServerlessSpec(cloud="aws", region="us-east-1"),
            )

    @property
    def _index(self):
        self._ensure_index()
        return self._client.Index(self._index_name)

    async def upsert(self, *, namespace: str, records: list[VectorRecord]) -> None:
        vectors = []
        for record in records:
            scaled_dense, scaled_sparse = scale_hybrid(
                record.dense_vector, record.sparse_vector
            )
            vectors.append(
                {
                    "id": record.chunk_id,
                    "values": scaled_dense,
                    "sparse_values": scaled_sparse,
                    "metadata": record.metadata,
                }
            )
        await asyncio.to_thread(
            self._index.upsert, vectors=vectors, namespace=namespace
        )

    async def query(
        self,
        *,
        namespace: str,
        dense_vector: list[float],
        sparse_vector: SparseVector,
        top_k: int,
    ) -> list[ScoredChunk]:
        scaled_dense, scaled_sparse = scale_hybrid(dense_vector, sparse_vector)
        response = await asyncio.to_thread(
            self._index.query,
            vector=scaled_dense,
            sparse_vector=scaled_sparse,
            top_k=top_k,
            namespace=namespace,
            include_metadata=True,
        )
        return [
            ScoredChunk(
                chunk_id=match["id"],
                score=match["score"],
                metadata=match.get("metadata") or {},
            )
            for match in response.get("matches", [])
        ]

    async def delete(self, *, namespace: str, ids: list[str]) -> None:
        await asyncio.to_thread(self._index.delete, ids=ids, namespace=namespace)

    async def delete_by_prefix(self, *, namespace: str, prefix: str) -> None:
        """Deletes every vector whose id starts with `prefix` -- used for
        document deletion instead of trusting Document.chunk_count, which
        can drift from what's actually in Pinecone (a failed/partial
        ingestion, a retried upsert, etc). Pinecone has no server-side
        "delete by prefix", so this lists matching ids (paginated, up to
        1000 per page) and deletes each page's batch."""

        def _list_and_delete() -> None:
            for page_ids in self._index.list(
                prefix=prefix, limit=1000, namespace=namespace
            ):
                if page_ids:
                    self._index.delete(ids=page_ids, namespace=namespace)

        await asyncio.to_thread(_list_and_delete)

    async def delete_namespace(self, *, namespace: str) -> None:
        await asyncio.to_thread(
            self._index.delete, delete_all=True, namespace=namespace
        )


@lru_cache
def _cached_provider(
    api_key: str, index_name: str, dimensions: int
) -> PineconeVectorStore:
    return PineconeVectorStore(api_key, index_name, dimensions)


def get_vector_store(*, dimensions: int) -> VectorStoreProvider:
    """Returns the operator-owned Pinecone index, sized for `dimensions`."""
    pinecone_config = get_settings().TRENCH_CONFIG.PINECONE
    return _cached_provider(
        pinecone_config.api_key, pinecone_config.index_name, dimensions
    )
