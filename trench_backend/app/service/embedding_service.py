"""Generates dense embedding vectors via Gemini's embedding API.

The embedding model is a fixed, code-level choice (not a database catalog
entry, not user-selectable) -- see the project's decision to keep exactly
one embedding model in play at a time, avoiding mixed-dimension vectors
inside one Pinecone namespace.

`gemini-embedding-001` natively outputs 3072-dim vectors but supports
Matryoshka-style truncation via `output_dimensionality` -- 768 is used here
to keep Pinecone storage/cost down while still comfortably exceeding
`text-embedding-004`'s fixed 768 dims in retrieval quality.

Per Google's docs (ai.google.dev/gemini-api/docs/embeddings),
gemini-embedding-001 only returns L2-normalized vectors at the default
3072 dimensions -- at a truncated output_dimensionality (768, here) the
API returns raw, *unnormalized* values, so every vector (document and
query alike) is normalized manually before use. Pinecone's "dotproduct"
metric needs unit-length dense vectors to behave like cosine similarity;
skipping this silently biases retrieval toward longer vectors.
"""

import math
from functools import lru_cache

from google import genai
from google.genai import types

from app.config import get_settings

MODEL_NAME = "gemini-embedding-001"
DIMENSIONS = 768

# Google's embed_content API rejects a `contents` list over 100 items in
# one call -- documents with more chunks than this would otherwise fail
# ingestion outright. Batches sequentially rather than concurrently to
# stay well clear of per-minute rate limits on top of the per-call cap.
_EMBED_BATCH_SIZE = 100


def _l2_normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


@lru_cache
def _client() -> genai.Client:
    return genai.Client(api_key=get_settings().TRENCH_CONFIG.GEMINI.api_key)


class EmbeddingService:
    MODEL_NAME = MODEL_NAME
    DIMENSIONS = DIMENSIONS

    @staticmethod
    async def embed_texts(texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), _EMBED_BATCH_SIZE):
            batch = texts[i : i + _EMBED_BATCH_SIZE]
            response = await _client().aio.models.embed_content(
                model=MODEL_NAME,
                contents=batch,
                config=types.EmbedContentConfig(
                    task_type="RETRIEVAL_DOCUMENT",
                    output_dimensionality=DIMENSIONS,
                ),
            )
            all_embeddings.extend(
                _l2_normalize(embedding.values) for embedding in response.embeddings
            )
        return all_embeddings

    @staticmethod
    async def embed_query(text: str) -> list[float]:
        response = await _client().aio.models.embed_content(
            model=MODEL_NAME,
            contents=[text],
            config=types.EmbedContentConfig(
                task_type="RETRIEVAL_QUERY",
                output_dimensionality=DIMENSIONS,
            ),
        )
        return _l2_normalize(response.embeddings[0].values)
