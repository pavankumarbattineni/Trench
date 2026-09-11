from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.database.models import Organization, User
from app.database.session import async_session_factory
from app.service.organization_service import OrganizationService
from app.service.retrieval_service import KnowledgeScope, RetrievalService
from app.service.vector_store_service import ScoredChunk

PREFIX = "test-retrieval-service"


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        # Organizations first -- Organization.owner_user_id FK-references
        # users, so deleting a user while their organization still
        # references them as owner would violate that constraint.
        org_result = await session.execute(
            select(Organization).where(Organization.name.like(f"{PREFIX}%"))
        )
        for organization in org_result.scalars().all():
            await session.delete(organization)
        await session.commit()

        result = await session.execute(
            select(User).where(User.email.like(f"{PREFIX}%"))
        )
        for user in result.scalars().all():
            await session.delete(user)
        await session.commit()


async def _make_user(session, suffix: str) -> User:
    user = User(
        email=f"{PREFIX}-{suffix}@example.com",
        username=f"{PREFIX.replace('-', '_')}_{suffix}",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.mark.asyncio
async def test_resolve_scope_personal_always_allowed():
    async with async_session_factory() as session:
        user = await _make_user(session, "personal")
        scope = await RetrievalService.resolve_scope(
            session, requesting_user_id=user.id, knowledge_type="personal"
        )
        assert scope.knowledge_type == "personal"
        assert scope.user_id == user.id
        assert scope.namespace == f"personal:{user.id}"


@pytest.mark.asyncio
async def test_resolve_scope_company_denied_without_membership():
    async with async_session_factory() as session:
        user = await _make_user(session, "no-org")
        with pytest.raises(HTTPException) as exc_info:
            await RetrievalService.resolve_scope(
                session, requesting_user_id=user.id, knowledge_type="company"
            )
        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_resolve_scope_company_allowed_for_admin():
    async with async_session_factory() as session:
        admin = await _make_user(session, "org-admin")
        organization, _member, _auto_added = await OrganizationService.create(
            session, creator=admin, name=f"{PREFIX}-org"
        )
        scope = await RetrievalService.resolve_scope(
            session, requesting_user_id=admin.id, knowledge_type="company"
        )
        assert scope.knowledge_type == "company"
        assert scope.organization_id == organization.id
        assert scope.namespace == f"company:{organization.id}"


@pytest.mark.asyncio
async def test_retrieve_maps_pinecone_matches_to_retrieved_chunks():
    fake_match = ScoredChunk(
        chunk_id="doc-1:0",
        score=0.42,
        metadata={
            "document_id": "doc-1",
            "document_name": "notes.txt",
            "chunk_index": 0,
            "text": "vacation policy details",
        },
    )

    class _FakeVectorStore:
        async def query(self, *, namespace, dense_vector, sparse_vector, top_k):
            return [fake_match]

    async def _fake_embed_query(text: str) -> list[float]:
        return [0.0] * 8

    with (
        patch(
            "app.service.retrieval_service.get_vector_store",
            return_value=_FakeVectorStore(),
        ),
        patch(
            "app.service.retrieval_service.EmbeddingService.embed_query",
            _fake_embed_query,
        ),
    ):
        results = await RetrievalService.retrieve(
            query="what is the vacation policy",
            scope=KnowledgeScope(
                knowledge_type="personal", user_id=None, organization_id=None
            ),
        )

    assert len(results) == 1
    assert results[0].chunk_id == "doc-1:0"
    assert results[0].document_name == "notes.txt"
    assert results[0].content == "vacation policy details"
    assert results[0].score == 0.42
