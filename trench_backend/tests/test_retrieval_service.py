from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select, update

from app.database.models import Tenant, User
from app.database.session import async_session_factory
from app.service.retrieval_service import KnowledgeScope, RetrievalService
from app.service.tenant_service import TenantService
from app.service.vector_store_service import ScoredChunk

PREFIX = "test-retrieval-service"


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        # users.tenant_id FK-references tenants with no ondelete, so a
        # user's tenant_id must be cleared before the tenant itself can be
        # deleted.
        await session.execute(
            update(User)
            .where(User.email.like(f"{PREFIX}%"))
            .values(tenant_id=None)
        )
        await session.commit()

        tenant_result = await session.execute(
            select(Tenant).where(Tenant.name.like(f"{PREFIX}%"))
        )
        for tenant in tenant_result.scalars().all():
            await session.delete(tenant)
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
        user = await _make_user(session, "no-tenant")
        with pytest.raises(HTTPException) as exc_info:
            await RetrievalService.resolve_scope(
                session, requesting_user_id=user.id, knowledge_type="company"
            )
        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_resolve_scope_company_allowed_for_admin():
    async with async_session_factory() as session:
        admin = await _make_user(session, "tenant-admin")
        tenant = await TenantService.create(
            session, creator=admin, name=f"{PREFIX}-tenant"
        )
        scope = await RetrievalService.resolve_scope(
            session, requesting_user_id=admin.id, knowledge_type="company"
        )
        assert scope.knowledge_type == "company"
        assert scope.tenant_id == tenant.id
        assert scope.namespace == f"company:{tenant.id}"


async def _make_member(session, suffix: str, *, tenant_id, granted: bool) -> User:
    member = User(
        email=f"{PREFIX}-{suffix}@example.com",
        username=f"{PREFIX.replace('-', '_')}_{suffix}",
        tenant_id=tenant_id,
        role="member",
        has_company_knowledge_access=granted,
    )
    session.add(member)
    await session.commit()
    return member


@pytest.mark.asyncio
async def test_resolve_scope_company_requires_a_grant_for_a_plain_member():
    async with async_session_factory() as session:
        owner = await _make_user(session, "grant-owner")
        tenant = await TenantService.create(
            session, creator=owner, name=f"{PREFIX}-grant-tenant"
        )
        ungranted = await _make_member(
            session, "ungranted", tenant_id=tenant.id, granted=False
        )
        granted = await _make_member(
            session, "granted", tenant_id=tenant.id, granted=True
        )

        with pytest.raises(HTTPException) as exc_info:
            await RetrievalService.resolve_scope(
                session, requesting_user_id=ungranted.id, knowledge_type="company"
            )
        assert exc_info.value.status_code == 403

        scope = await RetrievalService.resolve_scope(
            session, requesting_user_id=granted.id, knowledge_type="company"
        )
        assert scope.tenant_id == tenant.id
        assert scope.namespace == f"company:{tenant.id}"


@pytest.mark.asyncio
async def test_resolve_scope_company_denied_for_a_stale_grant_without_a_tenant():
    """A grant flag without a tenant (shouldn't happen -- removal resets
    both -- but must still fail closed) never authorizes anything."""
    async with async_session_factory() as session:
        orphan = await _make_member(
            session, "orphan-grant", tenant_id=None, granted=True
        )
        with pytest.raises(HTTPException) as exc_info:
            await RetrievalService.resolve_scope(
                session, requesting_user_id=orphan.id, knowledge_type="company"
            )
        assert exc_info.value.status_code == 403


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
                knowledge_type="personal", user_id=None, tenant_id=None
            ),
        )

    assert len(results) == 1
    assert results[0].chunk_id == "doc-1:0"
    assert results[0].document_name == "notes.txt"
    assert results[0].content == "vacation policy details"
    assert results[0].score == 0.42
