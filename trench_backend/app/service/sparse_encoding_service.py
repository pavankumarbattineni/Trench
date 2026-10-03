"""Sparse (BM25-style keyword) vector encoding for Pinecone hybrid search.

Uses pinecone-text's pretrained BM25 encoder (fit over a general corpus,
not this app's own documents) -- no per-user/per-corpus fitting step is
needed, matching "no user-specific config" for retrieval. This is what
supplies the "keyword/sparse" half of hybrid retrieval now that there's no
Postgres document_chunks table to full-text-search directly.
"""

from functools import lru_cache

from pinecone_text.sparse import BM25Encoder

SparseVector = dict[str, list]


@lru_cache
def _encoder() -> BM25Encoder:
    return BM25Encoder.default()


class SparseEncodingService:
    @staticmethod
    def encode_documents(texts: list[str]) -> list[SparseVector]:
        if not texts:
            return []
        return _encoder().encode_documents(texts)

    @staticmethod
    def encode_query(text: str) -> SparseVector:
        return _encoder().encode_queries(text)
