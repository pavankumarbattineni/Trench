"""Per-query LLM usage & cost tracking: pricing, capture, storage, API."""

import asyncio
import time
from decimal import Decimal
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select, update

from app.database.models import Document, LLMUsage, Tenant, User
from app.database.session import async_session_factory
from app.service import llm_cost_service
from app.service.llm_client_service import (
    LLMClientService,
    ResolvedModel,
    _ProviderUsage,
)
from app.service.retrieval_service import RetrievedChunk
from app.utils import usage_tracking

PREFIX = "test-usage-tracking"
_EMAIL = f"owner@{PREFIX}.example.com"

# --- Pricing --------------------------------------------------------------


def test_cost_uses_litellm_prices_with_cached_tokens_discounted():
    # groq/openai/gpt-oss-120b: $0.15 in, $0.075 cached, $0.60 out per 1M.
    cost = llm_cost_service.compute_cost(
        provider_name="groq",
        model_name="openai/gpt-oss-120b",
        input_tokens=1_000_000,
        cached_input_tokens=400_000,
        output_tokens=1_000_000,
    )
    expected = (
        Decimal("0.6") * Decimal("0.15")
        + Decimal("0.4") * Decimal("0.075")
        + Decimal("0.60")
    )
    assert cost == pytest.approx(expected)


def test_unknown_model_is_unpriced_not_free():
    assert (
        llm_cost_service.compute_cost(
            provider_name="openai",
            model_name="no-such-model",
            input_tokens=10,
            cached_input_tokens=0,
            output_tokens=10,
        )
        is None
    )


def test_cached_tokens_never_exceed_input():
    full = llm_cost_service.compute_cost(
        provider_name="anthropic",
        model_name="claude-sonnet-5-5",
        input_tokens=100,
        cached_input_tokens=100,
        output_tokens=0,
    )
    clamped = llm_cost_service.compute_cost(
        provider_name="anthropic",
        model_name="claude-sonnet-5-5",
        input_tokens=100,
        cached_input_tokens=500,
        output_tokens=0,
    )
    assert clamped == full


def test_cache_savings():
    saved = llm_cost_service.cache_savings(
        provider_name="groq",
        model_name="openai/gpt-oss-120b",
        cached_input_tokens=1_000_000,
    )
    assert saved == pytest.approx(Decimal("0.075"))


# --- Capture in stream_generate ------------------------------------------


def _provider(deltas, usage=None):
    async def fake(resolved, *, system_prompt, messages):
        for delta in deltas:
            yield delta
        if usage is not None:
            yield usage

    return fake


async def _drain(resolved, monkeypatch, provider):
    monkeypatch.setattr(LLMClientService, "_stream_groq", provider)
    text = ""
    async for delta in LLMClientService.stream_generate(
        resolved, system_prompt="sys", messages=[{"role": "user", "content": "hi"}]
    ):
        text += delta
    return text


@pytest.mark.asyncio
async def test_reported_usage_is_recorded_and_never_shown_to_the_user(monkeypatch):
    resolved = ResolvedModel(
        provider_name="groq", model_name="openai/gpt-oss-120b", api_key="k"
    )
    usage = _ProviderUsage(input_tokens=120, cached_input_tokens=20, output_tokens=7)
    with (
        usage_tracking.collect_usage() as calls,
        usage_tracking.usage_step("condense"),
    ):
        text = await _drain(resolved, monkeypatch, _provider(["Hel", "lo"], usage))

    assert text == "Hello"
    assert len(calls) == 1
    call = calls[0]
    assert (call.step, call.provider_name, call.key_owner) == (
        "condense",
        "groq",
        "platform",
    )
    assert (call.input_tokens, call.cached_input_tokens, call.output_tokens) == (
        120,
        20,
        7,
    )
    assert call.estimated is False


@pytest.mark.asyncio
async def test_missing_usage_is_estimated_and_flagged(monkeypatch):
    resolved = ResolvedModel(
        provider_name="groq", model_name="m", api_key="k", key_owner="personal"
    )
    with usage_tracking.collect_usage() as calls:
        await _drain(resolved, monkeypatch, _provider(["some answer text"]))

    assert len(calls) == 1
    assert calls[0].estimated is True
    assert calls[0].output_tokens > 0
    assert calls[0].key_owner == "personal"


@pytest.mark.asyncio
async def test_nothing_is_recorded_outside_a_turn(monkeypatch):
    resolved = ResolvedModel(provider_name="groq", model_name="m", api_key="k")
    usage = _ProviderUsage(input_tokens=1, cached_input_tokens=0, output_tokens=1)
    # No collect_usage(): must behave exactly as before.
    text = await _drain(resolved, monkeypatch, _provider(["ok"], usage))
    assert text == "ok"


@pytest.mark.asyncio
async def test_a_stopped_stream_still_records_usage(monkeypatch):
    resolved = ResolvedModel(provider_name="groq", model_name="m", api_key="k")
    monkeypatch.setattr(
        LLMClientService, "_stream_groq", _provider(["one ", "two ", "three"])
    )
    with usage_tracking.collect_usage() as calls:
        stream = LLMClientService.stream_generate(
            resolved, system_prompt="s", messages=[]
        )
        first = await stream.__anext__()
        await stream.aclose()  # the user pressed Stop

    assert first == "one "
    assert len(calls) == 1 and calls[0].estimated is True


# --- End to end: chat turn -> stored rows -> API --------------------------


def _claims() -> dict:
    return {"uid": f"{PREFIX}-uid", "email": _EMAIL, "iat": int(time.time())}


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        users = (
            (await session.execute(select(User).where(User.email == _EMAIL)))
            .scalars()
            .all()
        )
        for user in users:
            await session.execute(delete(LLMUsage).where(LLMUsage.user_id == user.id))
        await session.execute(
            update(User).where(User.email == _EMAIL).values(tenant_id=None)
        )
        await session.commit()
        for tenant in (
            (
                await session.execute(
                    select(Tenant).where(Tenant.domain.like(f"{PREFIX}%"))
                )
            )
            .scalars()
            .all()
        ):
            await session.delete(tenant)
        for user in users:
            await session.delete(user)
        await session.commit()


async def _login(client: AsyncClient) -> None:
    with patch("app.utils.firebase.verify_firebase_id_token", return_value=_claims()):
        await client.post(
            "/api/v1/auth/signup/owner",
            json={"id_token": "fake", "tenant_name": f"{PREFIX}-org"},
        )
        response = await client.post("/api/v1/auth/login", json={"id_token": "fake"})
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    async with async_session_factory() as session:
        user = (
            await session.execute(select(User).where(User.email == _EMAIL))
        ).scalar_one()
        session.add(
            Document(
                user_id=user.id,
                document_name="seed.txt",
                document_type="txt",
                mime_type="text/plain",
                storage_path=f"{PREFIX}/seed.txt",
                file_size=1,
                content_hash=f"{PREFIX}-hash",
                knowledge_type="personal",
                knowledge_base="own",
                status="completed",
            )
        )
        await session.commit()


async def _fake_retrieve(*, query, scope, top_k=8):
    return [
        RetrievedChunk(
            chunk_id="c1",
            document_id="d1",
            document_name="notes.txt",
            chunk_index=0,
            content="Trench usage test content",
            score=0.95,
        )
    ]


async def _passthrough_rerank(query, chunks, *, top_k=8):
    return chunks[:top_k]


@pytest.mark.asyncio
async def test_a_chat_turn_stores_usage_and_the_usage_api_reports_it(
    client: AsyncClient, monkeypatch
):
    await _login(client)

    async def groq_with_usage(resolved, *, system_prompt, messages):
        for word in ["Usage ", "is ", "tracked [1]."]:
            yield word
        yield _ProviderUsage(
            input_tokens=2000, cached_input_tokens=500, output_tokens=300
        )

    async def fake_title(db, query):
        return "Usage test"

    monkeypatch.setattr(LLMClientService, "_stream_groq", groq_with_usage)
    monkeypatch.setattr(
        "app.service.thread_title_service.ThreadTitleService._complete", fake_title
    )
    with (
        patch("app.graph.rag_graph.RetrievalService.retrieve", _fake_retrieve),
        patch("app.graph.rag_graph.RerankerService.rerank", _passthrough_rerank),
    ):
        thread = (await client.post("/api/v1/threads")).json()
        sent = await client.post(
            f"/api/v1/chat/threads/{thread['id']}/messages",
            json={"query": "How is usage tracked?", "knowledge_type": "personal"},
        )
        stream_id = sent.json()["stream_id"]
        for _ in range(100):
            status = (
                await client.get(f"/api/v1/chat/streams/{stream_id}/status")
            ).json()["status"]
            if status == "completed":
                break
            await asyncio.sleep(0.05)
        assert status == "completed"

        # Usage is written right after the turn finishes.
        for _ in range(50):
            async with async_session_factory() as session:
                rows = (
                    (
                        await session.execute(
                            select(LLMUsage).where(
                                LLMUsage.assistant_message_id == stream_id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            if rows:
                break
            await asyncio.sleep(0.05)

    generate = [r for r in rows if r.step == "generate"]
    assert len(generate) == 1
    row = generate[0]
    assert (row.input_tokens, row.cached_input_tokens, row.output_tokens) == (
        2000,
        500,
        300,
    )
    assert row.key_owner == "platform"
    assert row.query_preview == "How is usage tracked?"
    assert row.cost_usd == pytest.approx(
        llm_cost_service.compute_cost(
            provider_name="groq",
            model_name=row.model,
            input_tokens=2000,
            cached_input_tokens=500,
            output_tokens=300,
        )
    )

    summary = (await client.get("/api/v1/usage/summary?days=7&tz=Asia/Kolkata")).json()
    assert summary["current"]["questions"] == 1
    assert summary["current"]["input_tokens"] >= 2000
    assert len(summary["daily"]) == 7
    assert summary["by_model"][0]["model"] == row.model

    page = (await client.get("/api/v1/usage/queries?days=7")).json()
    assert page["total"] == 1
    item = page["items"][0]
    assert item["query_preview"] == "How is usage tracked?"
    assert item["thread_id"] == thread["id"]
    assert {s["step"] for s in item["steps"]} >= {"generate"}
