"""POST /api/v1/chat/threads/{thread_id}/messages' two request-level
guards: a 4000-char query cap (422) and a 20-req/min-per-user rate limit
(429, with Retry-After) -- see app/service/rate_limit_service.py."""

import asyncio
import time
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.database.models import Tenant, User
from app.database.session import async_session_factory
from app.service import rate_limit_service
from app.service.rate_limit_service import MAX_REQUESTS_PER_WINDOW

PREFIX = "test-chat-limits"


def _claims(email: str) -> dict:
    return {"uid": f"{PREFIX}-uid-{email}", "email": email, "iat": int(time.time())}


async def _login(client: AsyncClient, email: str) -> None:
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_claims(email)
    ):
        await client.post(
            "/api/v1/auth/signup/owner",
            json={"id_token": "fake", "tenant_name": f"{PREFIX}-org-{email}"},
        )
        response = await client.post("/api/v1/auth/login", json={"id_token": "fake"})
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"


@pytest.fixture(autouse=True)
async def cleanup():
    rate_limit_service._windows.clear()
    yield
    rate_limit_service._windows.clear()
    async with async_session_factory() as session:
        await session.execute(
            update(User).where(User.email.like(f"%{PREFIX}%")).values(tenant_id=None)
        )
        await session.commit()
        tenant_result = await session.execute(
            select(Tenant).where(Tenant.domain.like(f"%{PREFIX}%"))
        )
        for tenant in tenant_result.scalars().all():
            await session.delete(tenant)
        await session.commit()
        result = await session.execute(
            select(User).where(User.email.like(f"%{PREFIX}%"))
        )
        for user in result.scalars().all():
            await session.delete(user)
        await session.commit()


@pytest.mark.asyncio
async def test_query_over_4000_chars_is_rejected_with_422(client: AsyncClient):
    email = f"owner@{PREFIX}-length.example.com"
    await _login(client, email)
    thread = (await client.post("/api/v1/threads")).json()

    response = await client.post(
        f"/api/v1/chat/threads/{thread['id']}/messages",
        json={"query": "x" * 4001, "knowledge_type": "personal"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_query_at_4000_chars_is_accepted(client: AsyncClient):
    email = f"owner@{PREFIX}-length-ok.example.com"
    await _login(client, email)
    thread = (await client.post("/api/v1/threads")).json()

    async def _fake_retrieve(*, query, scope, top_k=8):
        return []

    async def _fake_stream_generate(resolved, *, system_prompt, messages):
        yield "ok"

    with (
        patch("app.graph.rag_graph.RetrievalService.retrieve", _fake_retrieve),
        patch(
            "app.service.llm_client_service.LLMClientService.stream_generate",
            _fake_stream_generate,
        ),
    ):
        response = await client.post(
            f"/api/v1/chat/threads/{thread['id']}/messages",
            json={"query": "x" * 4000, "knowledge_type": "personal"},
        )

    assert response.status_code == 202


async def _wait_for_status(
    client: AsyncClient, stream_id: str, target: str = "completed", timeout: float = 5.0
):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = await client.get(f"/api/v1/chat/streams/{stream_id}/status")
        if response.json()["status"] == target:
            return response.json()
        await asyncio.sleep(0.05)
    raise AssertionError(f"stream never reached status={target!r}")


@pytest.mark.asyncio
async def test_exceeding_the_per_minute_limit_returns_429_with_retry_after(
    client: AsyncClient,
):
    email = f"owner@{PREFIX}-ratelimit.example.com"
    await _login(client, email)
    thread = (await client.post("/api/v1/threads")).json()

    async def _fake_retrieve(*, query, scope, top_k=8):
        return []

    async def _fake_stream_generate(resolved, *, system_prompt, messages):
        yield "ok"

    with (
        patch("app.graph.rag_graph.RetrievalService.retrieve", _fake_retrieve),
        patch(
            "app.service.llm_client_service.LLMClientService.stream_generate",
            _fake_stream_generate,
        ),
    ):
        for _ in range(MAX_REQUESTS_PER_WINDOW):
            response = await client.post(
                f"/api/v1/chat/threads/{thread['id']}/messages",
                json={"query": "hi", "knowledge_type": "personal"},
            )
            assert response.status_code == 202
            await _wait_for_status(client, response.json()["stream_id"])

        over_limit = await client.post(
            f"/api/v1/chat/threads/{thread['id']}/messages",
            json={"query": "one too many", "knowledge_type": "personal"},
        )

    assert over_limit.status_code == 429
    assert "Retry-After" in over_limit.headers
    assert int(over_limit.headers["Retry-After"]) > 0
