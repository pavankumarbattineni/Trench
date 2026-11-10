"""Sparse (keyword) vector encoding for Pinecone hybrid search.

Uses Pinecone's hosted sparse embedding model, `pinecone-sparse-english-v0`,
through the Pinecone Inference API -- the "keyword/sparse" half of hybrid
retrieval (the dense half is EmbeddingService). Hosted rather than local on
purpose: pinecone-text's pretrained BM25 encoder held ~3.9M vocabulary terms
in Python dicts (~400 MB of RAM, downloaded on first use after every
start), which doesn't fit small container hosts.

Documents and queries are encoded with different `input_type`s ("passage"
vs "query"), as the model requires; both return {"indices", "values"} in the
same shape Pinecone's sparse_values expects. Changing this model changes
every stored sparse vector -- existing vectors must be re-encoded (see
scripts/reindex_sparse_vectors.py).
"""

import asyncio
from functools import lru_cache

from pinecone import Pinecone

from app.config import get_settings

MODEL_NAME = "pinecone-sparse-english-v0"
# The Inference API accepts at most 96 inputs per request.
_BATCH_SIZE = 96
# Passages (contextualized chunks: document name + heading path + ~512-token
# chunk) can exceed the model's 512-token default; use its 2,048 maximum so
# chunk text isn't silently cut off. Longer input is truncated at the end.
_PASSAGE_MAX_TOKENS = 2048

SparseVector = dict[str, list]


@lru_cache
def _client() -> Pinecone:
    return Pinecone(api_key=get_settings().TRENCH_CONFIG.PINECONE.api_key)


def _embed(texts: list[str], parameters: dict) -> list[SparseVector]:
    response = _client().inference.embed(
        model=MODEL_NAME, inputs=texts, parameters=parameters
    )
    return [
        {"indices": list(item.sparse_indices), "values": list(item.sparse_values)}
        for item in response.data
    ]


class SparseEncodingService:
    MODEL_NAME = MODEL_NAME

    @staticmethod
    async def encode_documents(texts: list[str]) -> list[SparseVector]:
        if not texts:
            return []
        parameters = {
            "input_type": "passage",
            "truncate": "END",
            "max_tokens_per_sequence": _PASSAGE_MAX_TOKENS,
        }
        vectors: list[SparseVector] = []
        for start in range(0, len(texts), _BATCH_SIZE):
            batch = texts[start : start + _BATCH_SIZE]
            vectors.extend(await asyncio.to_thread(_embed, batch, parameters))
        return vectors

    @staticmethod
    async def encode_query(text: str) -> SparseVector:
        parameters = {"input_type": "query", "truncate": "END"}
        [vector] = await asyncio.to_thread(_embed, [text], parameters)
        return vector
