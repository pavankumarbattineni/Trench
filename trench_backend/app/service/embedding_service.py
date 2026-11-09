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

Rate limits: Google counts every text in a batched embed_content call
toward the key's per-minute quota (100/minute on the free tier), so one
large document can exhaust it. Every call goes through `_RateLimiter`,
which waits rather than exceed TRENCH_CONFIG.GEMINI.embed_requests_per_minute,
and a 429 that still happens (e.g. another process using the same key) is
retried after Google's suggested delay instead of failing the upload/turn.
"""

import asyncio
import logging
import math
import re
import time
from collections import deque
from functools import lru_cache

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from app.config import get_settings

logger = logging.getLogger(__name__)

MODEL_NAME = "gemini-embedding-001"
DIMENSIONS = 768

# Google's embed_content API rejects a `contents` list over 100 items in
# one call -- documents with more chunks than this would otherwise fail
# ingestion outright. Batches sequentially rather than concurrently.
_EMBED_BATCH_SIZE = 100

_WINDOW_SECONDS = 60.0
# How many times a 429 is retried before giving up, and the backoff used
# when Google's response carries no retry delay of its own.
_MAX_RATE_LIMIT_RETRIES = 5
_FALLBACK_BACKOFF_SECONDS = 5.0
_MAX_BACKOFF_SECONDS = 60.0

# Indirections so tests can drive a fake clock instead of really waiting.
_now = time.monotonic
_sleep = asyncio.sleep


def _l2_normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


@lru_cache
def _client() -> genai.Client:
    return genai.Client(api_key=get_settings().TRENCH_CONFIG.GEMINI.api_key)


def _items_per_minute() -> int:
    return max(1, get_settings().TRENCH_CONFIG.GEMINI.embed_requests_per_minute)


class _RateLimiter:
    """Sliding-window budget of embedded texts per minute, shared by every
    embedding call in this process. `acquire(n)` waits until sending `n`
    more texts keeps the last 60 seconds within the limit. Per-process
    only -- several workers sharing one key would each get the full
    budget, which the 429 retry below then absorbs."""

    def __init__(self) -> None:
        self._sent: deque[tuple[float, int]] = deque()  # (timestamp, count)
        self._lock = asyncio.Lock()

    async def acquire(self, count: int) -> None:
        limit = _items_per_minute()
        count = min(count, limit)  # a batch is never larger than the limit
        async with self._lock:
            while True:
                now = _now()
                while self._sent and now - self._sent[0][0] >= _WINDOW_SECONDS:
                    self._sent.popleft()
                used = sum(n for _, n in self._sent)
                if used + count <= limit:
                    self._sent.append((now, count))
                    return
                # Wait until the oldest send leaves the window, then re-check.
                wait = _WINDOW_SECONDS - (now - self._sent[0][0]) + 0.05
                logger.info(
                    "Embedding rate limit: waiting %.1fs (%d/%d texts this minute)",
                    wait,
                    used,
                    limit,
                )
                await _sleep(wait)


_rate_limiter = _RateLimiter()


def _retry_delay_seconds(error: genai_errors.APIError) -> float | None:
    """Google's suggested wait from a 429 body's RetryInfo ("4s",
    "4.285s"), if present."""
    details = error.details if isinstance(error.details, dict) else {}
    body = details.get("error", details)
    for detail in body.get("details", []) if isinstance(body, dict) else []:
        delay = detail.get("retryDelay") if isinstance(detail, dict) else None
        match = re.fullmatch(r"([\d.]+)s", str(delay)) if delay else None
        if match:
            return float(match.group(1))
    return None


async def _embed(contents: list[str], task_type: str):
    """One embed_content call, paced by the rate limiter and retried on
    429 RESOURCE_EXHAUSTED. Any other error propagates immediately."""
    for attempt in range(_MAX_RATE_LIMIT_RETRIES + 1):
        await _rate_limiter.acquire(len(contents))
        try:
            return await _client().aio.models.embed_content(
                model=MODEL_NAME,
                contents=contents,
                config=types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=DIMENSIONS,
                ),
            )
        except genai_errors.APIError as exc:
            if exc.code != 429 or attempt == _MAX_RATE_LIMIT_RETRIES:
                raise
            delay = _retry_delay_seconds(exc)
            if delay is None:
                delay = _FALLBACK_BACKOFF_SECONDS * (2**attempt)
            delay = min(delay + 0.5, _MAX_BACKOFF_SECONDS)
            logger.warning(
                "Gemini embedding quota hit (429); retrying in %.1fs (attempt %d/%d)",
                delay,
                attempt + 1,
                _MAX_RATE_LIMIT_RETRIES,
            )
            await _sleep(delay)
    raise AssertionError("unreachable")  # loop always returns or raises


class EmbeddingService:
    MODEL_NAME = MODEL_NAME
    DIMENSIONS = DIMENSIONS

    @staticmethod
    async def embed_texts(texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # Never send more texts in one call than the per-minute budget
        # allows, or the call could never fit inside the window.
        batch_size = min(_EMBED_BATCH_SIZE, _items_per_minute())
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            response = await _embed(batch, "RETRIEVAL_DOCUMENT")
            all_embeddings.extend(
                _l2_normalize(embedding.values) for embedding in response.embeddings
            )
        return all_embeddings

    @staticmethod
    async def embed_query(text: str) -> list[float]:
        response = await _embed([text], "RETRIEVAL_QUERY")
        return _l2_normalize(response.embeddings[0].values)
