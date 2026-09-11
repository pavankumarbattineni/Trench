import asyncio
import time
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.database.models import User
from app.database.session import async_session_factory

PREFIX = "test-chat-flow"


def _claims() -> dict:
    return {
        "uid": f"{PREFIX}-uid",
        "email": f"{PREFIX}@example.com",
        "iat": int(time.time()),
    }


async def _login(client: AsyncClient) -> None:
    """Signs up (idempotently -- a 409 for an already-registered email is
    fine here) then signs in, since login no longer lazily creates a
    user."""
    with patch("app.utils.firebase.verify_firebase_id_token", return_value=_claims()):
        await client.post("/api/v1/auth/signup", json={"id_token": "fake"})
        response = await client.post("/api/v1/auth/login", json={"id_token": "fake"})
    client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        result = await session.execute(
            select(User).where(User.email.like(f"{PREFIX}%"))
        )
        for user in result.scalars().all():
            await session.delete(user)
        await session.commit()


async def _fake_stream_generate(resolved, *, system_prompt, messages):
    for word in ["Hello", " ", "there", "!"]:
        yield word


async def _fake_retrieve(*, query, scope, top_k=8):
    # Keeps these tests from making real Pinecone/BM25-encoder network
    # calls -- retrieval itself is covered separately (see
    # test_retrieval_service.py and the manual live verification).
    return []


@pytest.fixture(autouse=True)
def _fake_title_generation(monkeypatch):
    """Every test here sends a thread's first message, which now also
    triggers title generation -- avoid real Groq calls (see
    test_thread_title_service.py for that behavior's own coverage)."""

    async def _fake_complete(db, query):
        return "Fake Title"

    monkeypatch.setattr(
        "app.service.thread_title_service.ThreadTitleService._complete",
        _fake_complete,
    )


async def _wait_for_status(
    client: AsyncClient, stream_id: str, target: str, timeout: float = 5.0
):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = await client.get(f"/api/v1/chat/streams/{stream_id}/status")
        if response.json()["status"] == target:
            return response.json()
        await asyncio.sleep(0.05)
    raise AssertionError(f"stream never reached status={target!r}")


async def _wait_for_title(client: AsyncClient, thread_id: str, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = await client.get(f"/api/v1/threads/{thread_id}")
        title = response.json()["thread"]["title"]
        if title is not None:
            return title
        await asyncio.sleep(0.05)
    raise AssertionError("thread was never titled")


@pytest.mark.asyncio
async def test_send_message_streams_and_persists_chat_history(client: AsyncClient):
    await _login(client)

    thread = (await client.post("/api/v1/threads")).json()

    with (
        patch(
            "app.service.llm_client_service.LLMClientService.stream_generate",
            _fake_stream_generate,
        ),
        patch("app.graph.rag_graph.RetrievalService.retrieve", _fake_retrieve),
    ):
        send_response = await client.post(
            f"/api/v1/chat/threads/{thread['id']}/messages",
            json={"query": "What's in my notes?", "knowledge_type": "personal"},
        )
        assert send_response.status_code == 202
        accepted = send_response.json()
        assert accepted["status"] == "pending"

        final = await _wait_for_status(client, accepted["stream_id"], "completed")
        assert final["content"] == "Hello there!"

    detail = (await client.get(f"/api/v1/threads/{thread['id']}")).json()
    assert len(detail["messages"]) == 2
    assert detail["messages"][0]["role"] == "user"
    assert detail["messages"][1]["role"] == "assistant"
    assert detail["messages"][1]["content"] == "Hello there!"
    assert detail["messages"][1]["status"] == "completed"


@pytest.mark.asyncio
async def test_first_message_triggers_title_generation_but_second_does_not(
    client: AsyncClient, monkeypatch
):
    await _login(client)

    call_count = 0

    async def _counting_complete(db, query):
        nonlocal call_count
        call_count += 1
        return "Fake Title"

    monkeypatch.setattr(
        "app.service.thread_title_service.ThreadTitleService._complete",
        _counting_complete,
    )

    thread = (await client.post("/api/v1/threads")).json()
    assert thread["title"] is None

    with (
        patch(
            "app.service.llm_client_service.LLMClientService.stream_generate",
            _fake_stream_generate,
        ),
        patch("app.graph.rag_graph.RetrievalService.retrieve", _fake_retrieve),
    ):
        first = await client.post(
            f"/api/v1/chat/threads/{thread['id']}/messages",
            json={"query": "What's in my notes?", "knowledge_type": "personal"},
        )
        await _wait_for_status(client, first.json()["stream_id"], "completed")
        assert await _wait_for_title(client, thread["id"]) == "Fake Title"
        assert call_count == 1

        second = await client.post(
            f"/api/v1/chat/threads/{thread['id']}/messages",
            json={"query": "And what else?", "knowledge_type": "personal"},
        )
        await _wait_for_status(client, second.json()["stream_id"], "completed")

    # Give a would-be (incorrect) second title-generation task a moment to
    # run, so this isn't a false negative from checking too early.
    await asyncio.sleep(0.2)
    assert call_count == 1
    detail = (await client.get(f"/api/v1/threads/{thread['id']}")).json()
    assert detail["thread"]["title"] == "Fake Title"


@pytest.mark.asyncio
async def test_company_query_denied_without_access(client: AsyncClient):
    await _login(client)

    thread = (await client.post("/api/v1/threads")).json()

    with (
        patch(
            "app.service.llm_client_service.LLMClientService.stream_generate",
            _fake_stream_generate,
        ),
        patch("app.graph.rag_graph.RetrievalService.retrieve", _fake_retrieve),
    ):
        send_response = await client.post(
            f"/api/v1/chat/threads/{thread['id']}/messages",
            json={"query": "What's our leave policy?", "knowledge_type": "company"},
        )
        accepted = send_response.json()
        final = await _wait_for_status(client, accepted["stream_id"], "failed")
        assert "access" in final["content"].lower()


@pytest.mark.asyncio
async def test_interrupt_marks_stream_interrupted(client: AsyncClient):
    await _login(client)

    thread = (await client.post("/api/v1/threads")).json()

    async def _slow_stream_generate(resolved, *, system_prompt, messages):
        for _ in range(50):
            await asyncio.sleep(0.05)
            yield "word "

    with (
        patch(
            "app.service.llm_client_service.LLMClientService.stream_generate",
            _slow_stream_generate,
        ),
        patch("app.graph.rag_graph.RetrievalService.retrieve", _fake_retrieve),
    ):
        send_response = await client.post(
            f"/api/v1/chat/threads/{thread['id']}/messages",
            json={"query": "Tell me something long", "knowledge_type": "personal"},
        )
        stream_id = send_response.json()["stream_id"]

        await asyncio.sleep(0.15)
        interrupt_response = await client.delete(f"/api/v1/chat/streams/{stream_id}")
        assert interrupt_response.status_code == 204

        final = await _wait_for_status(client, stream_id, "interrupted")
        assert final["status"] == "interrupted"


@pytest.mark.asyncio
async def test_second_message_includes_prior_turn_in_generation_context(
    client: AsyncClient,
):
    """The agent must be genuinely conversational: a second turn's actual
    generation call has to see both what was asked AND what the assistant
    answered in the first turn, not just the new turn's bare query in
    isolation (which is what caused a follow-up like "continue" to get a
    contextless, unanswerable prompt)."""
    await _login(client)
    thread = (await client.post("/api/v1/threads")).json()

    captured_calls = []

    async def _capturing_stream_generate(resolved, *, system_prompt, messages):
        captured_calls.append({"system_prompt": system_prompt, "messages": messages})
        for word in ["Hello", " ", "there", "!"]:
            yield word

    with (
        patch(
            "app.service.llm_client_service.LLMClientService.stream_generate",
            _capturing_stream_generate,
        ),
        patch("app.graph.rag_graph.RetrievalService.retrieve", _fake_retrieve),
    ):
        first = await client.post(
            f"/api/v1/chat/threads/{thread['id']}/messages",
            json={"query": "What's in my notes?", "knowledge_type": "personal"},
        )
        await _wait_for_status(client, first.json()["stream_id"], "completed")

        second = await client.post(
            f"/api/v1/chat/threads/{thread['id']}/messages",
            json={"query": "continue", "knowledge_type": "personal"},
        )
        await _wait_for_status(client, second.json()["stream_id"], "completed")

    # Distinguish the real `generate` node's call (its system prompt is the
    # RAG assistant prompt) from `condense_query`'s separate rewrite call.
    generate_calls = [
        c for c in captured_calls if "knowledge assistant" in c["system_prompt"]
    ]
    assert len(generate_calls) == 2
    second_turn_contents = [m["content"] for m in generate_calls[1]["messages"]]
    assert "What's in my notes?" in second_turn_contents
    assert "Hello there!" in second_turn_contents
    assert second_turn_contents[-1] == "continue"


@pytest.mark.asyncio
async def test_continue_after_interruption_carries_partial_answer_forward(
    client: AsyncClient,
):
    """After a turn is interrupted mid-stream, a follow-up "continue" must
    still see the partial answer that was already given -- not treat the
    interruption as if nothing had been said at all."""
    await _login(client)
    thread = (await client.post("/api/v1/threads")).json()

    async def _slow_stream_generate(resolved, *, system_prompt, messages):
        for word in ["Partial", " ", "answer"]:
            await asyncio.sleep(0.05)
            yield word
        for _ in range(50):
            await asyncio.sleep(0.05)
            yield " more"

    with (
        patch(
            "app.service.llm_client_service.LLMClientService.stream_generate",
            _slow_stream_generate,
        ),
        patch("app.graph.rag_graph.RetrievalService.retrieve", _fake_retrieve),
    ):
        first = await client.post(
            f"/api/v1/chat/threads/{thread['id']}/messages",
            json={"query": "Tell me something long", "knowledge_type": "personal"},
        )
        stream_id = first.json()["stream_id"]

        await asyncio.sleep(0.2)
        interrupt_response = await client.delete(f"/api/v1/chat/streams/{stream_id}")
        assert interrupt_response.status_code == 204
        await _wait_for_status(client, stream_id, "interrupted")

        captured_calls = []

        async def _capturing_stream_generate(resolved, *, system_prompt, messages):
            captured_calls.append(messages)
            for word in ["Hello", " ", "there", "!"]:
                yield word

        with patch(
            "app.service.llm_client_service.LLMClientService.stream_generate",
            _capturing_stream_generate,
        ):
            second = await client.post(
                f"/api/v1/chat/threads/{thread['id']}/messages",
                json={"query": "continue", "knowledge_type": "personal"},
            )
            await _wait_for_status(client, second.json()["stream_id"], "completed")

    generate_call_contents = [m["content"] for m in captured_calls[-1]]
    assert any("Partial answer" in c for c in generate_call_contents)
