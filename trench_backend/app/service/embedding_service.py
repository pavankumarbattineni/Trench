"""Generates dense embedding vectors via Gemini's embedding API.

The embedding model is a fixed, code-level choice (not a database catalog
entry, not user-selectable) -- see the project's decision to keep exactly
one embedding model in play at a time, avoiding mixed-dimension vectors
inside one Pinecone namespace.

`gemini-embedding-001` natively outputs 3072-dim vectors but supports
Matryoshka-style truncation via `output_dimensionality` -- 768 is used here
to keep Pinecone storage/cost down while still comfortably exceeding
`text-embedding-004`'s fixed 768 dims in retrieval quality.
"""

from functools import lru_cache

from google import genai
from google.genai import types

from app.config import get_settings

MODEL_NAME = "gemini-embedding-001"
DIMENSIONS = 768


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
        response = await _client().aio.models.embed_content(
            model=MODEL_NAME,
            contents=texts,
            config=types.EmbedContentConfig(
                task_type="RETRIEVAL_DOCUMENT",
                output_dimensionality=DIMENSIONS,
            ),
        )
        return [embedding.values for embedding in response.embeddings]

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
        return response.embeddings[0].values
