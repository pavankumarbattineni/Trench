"""validate_knowledge_access's knowledge_base_empty check, and the
routing decision it feeds -- distinguishing "this scope has zero
documents" from "nothing matched this query" (see rag_graph.py's
generate() for where that distinction changes the system prompt)."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.graph.rag_graph import _route_after_access_check, validate_knowledge_access
from app.service.retrieval_service import KnowledgeScope

_USER_ID = uuid.uuid4()
_TENANT_ID = uuid.uuid4()


@pytest.mark.asyncio
async def test_personal_scope_with_no_documents_is_flagged_empty():
    scope = KnowledgeScope(knowledge_type="personal", user_id=_USER_ID, tenant_id=None)
    with (
        patch(
            "app.graph.rag_graph.RetrievalService.resolve_scope",
            AsyncMock(return_value=scope),
        ),
        patch(
            "app.graph.rag_graph.DocumentService.has_any_personal_documents",
            AsyncMock(return_value=False),
        ),
    ):
        result = await validate_knowledge_access(
            {"user_id": str(_USER_ID), "knowledge_type": "personal"},
            {"configurable": {"db": object()}},
        )

    assert result["access_denied"] is False
    assert result["knowledge_base_empty"] is True


@pytest.mark.asyncio
async def test_personal_scope_with_a_document_is_not_flagged_empty():
    scope = KnowledgeScope(knowledge_type="personal", user_id=_USER_ID, tenant_id=None)
    with (
        patch(
            "app.graph.rag_graph.RetrievalService.resolve_scope",
            AsyncMock(return_value=scope),
        ),
        patch(
            "app.graph.rag_graph.DocumentService.has_any_personal_documents",
            AsyncMock(return_value=True),
        ),
    ):
        result = await validate_knowledge_access(
            {"user_id": str(_USER_ID), "knowledge_type": "personal"},
            {"configurable": {"db": object()}},
        )

    assert result["knowledge_base_empty"] is False


@pytest.mark.asyncio
async def test_company_scope_checks_tenant_documents_not_personal():
    scope = KnowledgeScope(knowledge_type="company", user_id=None, tenant_id=_TENANT_ID)
    db = object()
    with (
        patch(
            "app.graph.rag_graph.RetrievalService.resolve_scope",
            AsyncMock(return_value=scope),
        ),
        patch(
            "app.graph.rag_graph.DocumentService.has_any_company_documents",
            AsyncMock(return_value=False),
        ) as has_company,
        patch(
            "app.graph.rag_graph.DocumentService.has_any_personal_documents",
            AsyncMock(return_value=True),
        ) as has_personal,
    ):
        result = await validate_knowledge_access(
            {"user_id": str(_USER_ID), "knowledge_type": "company"},
            {"configurable": {"db": db}},
        )

    assert result["knowledge_base_empty"] is True
    has_company.assert_awaited_once_with(db, tenant_id=_TENANT_ID)
    has_personal.assert_not_awaited()


@pytest.mark.asyncio
async def test_denied_access_short_circuits_before_the_document_check():
    with (
        patch(
            "app.graph.rag_graph.RetrievalService.resolve_scope",
            AsyncMock(side_effect=Exception("no access")),
        ),
        patch(
            "app.graph.rag_graph.DocumentService.has_any_personal_documents",
            AsyncMock(),
        ) as has_personal,
    ):
        result = await validate_knowledge_access(
            {"user_id": str(_USER_ID), "knowledge_type": "personal"},
            {"configurable": {"db": object()}},
        )

    assert result["access_denied"] is True
    has_personal.assert_not_awaited()


def test_routes_to_access_denied_when_denied():
    state = {"access_denied": True, "knowledge_base_empty": False}
    assert _route_after_access_check(state) == "access_denied"


def test_routes_to_generate_when_knowledge_base_is_empty():
    state = {"access_denied": False, "knowledge_base_empty": True}
    assert _route_after_access_check(state) == "generate"


def test_routes_to_condense_query_when_authorized_and_not_empty():
    state = {"access_denied": False, "knowledge_base_empty": False}
    assert _route_after_access_check(state) == "condense_query"
