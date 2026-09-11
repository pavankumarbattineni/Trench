"""Hybrid retrieval: a single Pinecone query combining a dense (semantic)
vector and a sparse (BM25 keyword) vector, scoped to exactly one knowledge
base namespace.

Authorization happens before any retrieval call is made -- see
`resolve_scope`, which is the single choke point every caller (chat
generation, future debugging endpoints) must go through. A denied request
never reaches the vector store.

There is no Postgres chunk table: chunk text and citation metadata come
back directly from Pinecone's own stored metadata.
"""

import uuid
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.service.embedding_service import EmbeddingService
from app.service.knowledge_access_service import KnowledgeAccessService
from app.service.sparse_encoding_service import SparseEncodingService
from app.service.vector_store_service import (
    company_namespace,
    get_vector_store,
    personal_namespace,
)

_COMPANY_ACCESS_DENIED = HTTPException(
    status.HTTP_403_FORBIDDEN,
    "You don't have access to this organization's company knowledge",
)


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    document_name: str
    chunk_index: int
    content: str
    score: float


@dataclass
class KnowledgeScope:
    knowledge_type: str
    user_id: uuid.UUID | None
    organization_id: uuid.UUID | None

    @property
    def namespace(self) -> str:
        if self.knowledge_type == "company":
            return company_namespace(self.organization_id)
        return personal_namespace(self.user_id)


class RetrievalService:
    @staticmethod
    async def resolve_scope(
        db: AsyncSession, *, requesting_user_id: uuid.UUID, knowledge_type: str
    ) -> KnowledgeScope:
        """Validates the requester may query `knowledge_type` and returns
        the resolved, trusted scope to retrieve against.

        Never trusts the caller's knowledge_type choice at face value for
        "company" -- membership + an active grant (or admin role) is
        required, checked fresh on every call.
        """
        if knowledge_type == "personal":
            return KnowledgeScope(
                knowledge_type="personal",
                user_id=requesting_user_id,
                organization_id=None,
            )

        organization_id = (
            await KnowledgeAccessService.authorized_company_organization_id(
                db, user_id=requesting_user_id
            )
        )
        if organization_id is None:
            raise _COMPANY_ACCESS_DENIED
        return KnowledgeScope(
            knowledge_type="company", user_id=None, organization_id=organization_id
        )

    @staticmethod
    async def retrieve(
        *, query: str, scope: KnowledgeScope, top_k: int = 8
    ) -> list[RetrievedChunk]:
        dense_vector = await EmbeddingService.embed_query(query)
        sparse_vector = SparseEncodingService.encode_query(query)

        vector_store = get_vector_store(dimensions=EmbeddingService.DIMENSIONS)
        matches = await vector_store.query(
            namespace=scope.namespace,
            dense_vector=dense_vector,
            sparse_vector=sparse_vector,
            top_k=top_k,
        )

        return [
            RetrievedChunk(
                chunk_id=match.chunk_id,
                document_id=match.metadata.get("document_id", ""),
                document_name=match.metadata.get("document_name", ""),
                chunk_index=match.metadata.get("chunk_index", 0),
                content=match.metadata.get("text", ""),
                score=match.score,
            )
            for match in matches
        ]
