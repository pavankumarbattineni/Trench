"""Reranks already-retrieved (hybrid dense+sparse) chunks against the
actual query using Cohere's hosted Rerank API -- more accurate than
Pinecone's retrieval-time scoring (a joint query+passage encoding vs.
independent dense/sparse vectors), run only over the top ~20-50 hybrid
results rather than the whole corpus.

Accessed through this one static method so swapping providers later
(a different hosted reranker, or a local cross-encoder) never touches
the retrieval pipeline that calls it -- only this module's internals
would change. Chosen over a local cross-encoder (sentence-transformers +
torch) specifically to avoid that dependency's weight.
"""

from functools import lru_cache

import cohere

from app.config import get_settings

MODEL_NAME = "rerank-v3.5"


@lru_cache
def _client() -> cohere.AsyncClientV2:
    return cohere.AsyncClientV2(
        api_key=get_settings().TRENCH_CONFIG.COHERE_RERANKER.api_key
    )


class RerankerService:
    @staticmethod
    async def rerank(
        query: str, chunks: list[dict], *, top_k: int = 8
    ) -> list[dict]:
        """Returns up to `top_k` of `chunks`, reordered by Cohere's
        relevance scoring for `query`. Returns [] unchanged (no API call)
        if `chunks` is empty -- there's nothing to rerank.

        Each returned chunk's "score" is overwritten with Cohere's own
        relevance_score for it -- callers downstream (e.g.
        assess_retrieval_sufficiency) read "score" expecting this
        reranked relevance number, not the Pinecone hybrid-search score
        the chunk carried in before reranking.
        """
        if not chunks:
            return []
        response = await _client().rerank(
            model=MODEL_NAME,
            query=query,
            documents=[chunk["content"] for chunk in chunks],
            top_n=top_k,
        )
        return [
            {**chunks[result.index], "score": result.relevance_score}
            for result in response.results
        ]
