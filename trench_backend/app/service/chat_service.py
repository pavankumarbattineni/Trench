"""Orchestrates a chat turn: persists the user message, spawns the LangGraph
run as a background asyncio task, and exposes status/cancel operations
against it.

Mirrors abyss_backend's stream lifecycle (background task + StreamManager
fan-out + status tracked in the persisted row + Task.cancel() for
interruption), scaled down to Trench's single-agent graph -- no tool
approval, no sub-agent dispatch.
"""

import asyncio
import logging
import uuid

from fastapi import HTTPException
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.checkpointer import get_checkpointer
from app.database.models import ChatHistory, Thread, User
from app.database.session import async_session_factory
from app.graph.rag_graph import build_graph
from app.service.thread_service import ThreadService
from app.service.thread_title_service import ThreadTitleService
from app.service.usage_service import UsageService
from app.utils import usage_tracking
from app.utils.stream_manager import stream_manager

logger = logging.getLogger(__name__)


def _user_facing_error(exc: Exception) -> str:
    """A failed turn's message as the user sees it. Provider SDK errors
    stringify to raw dumps (status codes, org ids, JSON bodies), so known
    failures get a plain explanation of what to do instead; the full error
    is logged for debugging."""
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = getattr(exc, "code", None)
    text = str(exc).lower()
    # Quota/billing first: e.g. Gemini's free tier has `limit: 0` for its
    # Pro models and OpenAI returns `insufficient_quota` -- no request to
    # that model can succeed on the key's current plan, whatever its size.
    if status == 429 and any(
        phrase in text
        for phrase in (
            "exceeded your current quota",
            "insufficient_quota",
            "quota exceeded",
        )
    ):
        return (
            "Your API key's plan doesn't include this model (quota exceeded). "
            "Enable billing for this provider's key, or choose another model."
        )
    # Only a genuinely oversized request (Groq: 413 "Request too large").
    if status == 413 or "request too large" in text:
        return (
            "This question, together with the conversation so far and the "
            "passages retrieved for it, is too large for the model's "
            "per-minute limit. Start a new chat or ask a shorter question, "
            "then try again."
        )
    if status == 429:
        return (
            "The model is receiving too many requests right now. Wait a "
            "minute and try again."
        )
    if status in (401, 403):
        return (
            "The API key for this model was rejected. Check it in "
            "Settings → Providers, or choose another model."
        )
    return "Something went wrong while generating this response. Please try again."


_compiled_graph = None

_ACTIVE_STATUSES = ("pending", "running")


def _get_compiled_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph().compile(checkpointer=get_checkpointer())
    return _compiled_graph


_THREAD_NOT_FOUND = HTTPException(http_status.HTTP_404_NOT_FOUND, "Thread not found")
_STREAM_NOT_FOUND = HTTPException(http_status.HTTP_404_NOT_FOUND, "Stream not found")
_ALREADY_RUNNING = HTTPException(
    http_status.HTTP_409_CONFLICT,
    "You already have a query running. Wait for it to complete, fail, or "
    "be interrupted before starting another.",
)


class ChatService:
    @staticmethod
    async def send_message(
        db: AsyncSession,
        *,
        user: User,
        thread: Thread,
        query: str,
        knowledge_type: str,
    ) -> ChatHistory:
        """Persists the user's message plus a `pending` assistant
        placeholder, then starts the graph run in the background.

        Returns the assistant ChatHistory row (its id is the stream_id).

        Raises:
            HTTPException: 409 if the user already has a query
                pending/running in ANY of their threads.
        """
        await ChatService._enforce_no_concurrent_query(db, user_id=user.id)
        is_new_session = not await ThreadService.has_any_messages(db, thread.id)

        # Two separate commits (not one batched insert) so the user row's
        # created_at is strictly earlier than the assistant's -- both would
        # otherwise get the same statement-level now() and sort ambiguously
        # in ThreadService.get_messages' created_at ordering.
        user_message = ChatHistory(
            thread_id=thread.id, role="user", content=query, status="completed"
        )
        db.add(user_message)
        await db.commit()

        assistant_message = ChatHistory(
            thread_id=thread.id, role="assistant", content="", status="pending"
        )
        db.add(assistant_message)
        await db.commit()
        await db.refresh(assistant_message)

        stream_id = str(assistant_message.id)
        task = asyncio.create_task(
            ChatService._run_generation(
                assistant_message_id=assistant_message.id,
                thread_id=thread.id,
                user=user,
                query=query,
                knowledge_type=knowledge_type,
                stream_id=stream_id,
            )
        )
        stream_manager.register_task(stream_id, task)

        if is_new_session:
            # Runs independently of the response-generation task above --
            # the title has no bearing on the chat turn itself, so it must
            # never block or fail it (see ThreadTitleService.generate).
            asyncio.create_task(
                ChatService._run_title_generation(
                    thread_id=thread.id,
                    query=query,
                    user_id=user.id,
                    assistant_message_id=assistant_message.id,
                )
            )

        return assistant_message

    @staticmethod
    async def _run_title_generation(
        *,
        thread_id: uuid.UUID,
        query: str,
        user_id: uuid.UUID | None = None,
        assistant_message_id: uuid.UUID | None = None,
    ) -> None:
        with (
            usage_tracking.collect_usage() as llm_calls,
            usage_tracking.usage_step("title"),
        ):
            async with async_session_factory() as db:
                await ThreadTitleService.generate(db, thread_id=thread_id, query=query)
        if user_id is not None:
            await UsageService.record_calls(
                user_id=user_id,
                thread_id=thread_id,
                assistant_message_id=assistant_message_id,
                query=query,
                calls=llm_calls,
            )

    @staticmethod
    async def _run_generation(
        *,
        assistant_message_id: uuid.UUID,
        thread_id: uuid.UUID,
        user: User,
        query: str,
        knowledge_type: str,
        stream_id: str,
    ) -> None:
        """Runs one chat turn and records the token usage of every LLM call
        it made -- whether the turn completed, was stopped or failed (all of
        them consumed tokens). Recording is best-effort and happens after
        the turn, so it can never affect the answer."""
        with usage_tracking.collect_usage() as llm_calls:
            await ChatService._run_generation_turn(
                assistant_message_id=assistant_message_id,
                thread_id=thread_id,
                user=user,
                query=query,
                knowledge_type=knowledge_type,
                stream_id=stream_id,
            )
        await UsageService.record_calls(
            user_id=user.id,
            thread_id=thread_id,
            assistant_message_id=assistant_message_id,
            query=query,
            calls=llm_calls,
        )

    @staticmethod
    async def _run_generation_turn(
        *,
        assistant_message_id: uuid.UUID,
        thread_id: uuid.UUID,
        user: User,
        query: str,
        knowledge_type: str,
        stream_id: str,
    ) -> None:
        # The whole body runs inside this try -- not just the ainvoke() call
        # -- because a Stop click races the background task from the moment
        # it's created: StreamManager.cancel_task() has no idea whether the
        # task has reached ainvoke() yet, so a fast cancel (or a fast
        # provider like Groq that lets the whole turn finish in well under a
        # second) can just as easily land while still fetching the message,
        # committing status="running", or re-fetching the user. Only
        # wrapping ainvoke() left every one of those earlier awaits
        # uncovered: a CancelledError raised there propagated straight out
        # of this task uncaught (asyncio doesn't log a bare CancelledError),
        # permanently stranding the row at "pending"/"running" and, via
        # _enforce_no_concurrent_query, wedging the user's account so every
        # later message 409'd.
        try:
            async with async_session_factory() as db:
                assistant_message = await db.get(ChatHistory, assistant_message_id)
                assistant_message.status = "running"
                await db.commit()

                # Re-fetch the user row in this task's own session rather than
                # reusing the `user` object captured by the HTTP request that
                # spawned this background task: this task can run seconds after
                # that request returned (202 Accepted), and the user may have
                # since changed their selected model (PATCH /users/me) -- using
                # the stale in-memory object would silently generate against
                # whatever model was selected *before* that change instead of
                # the user's current selection.
                user = await db.get(User, user.id) or user

                initial_state = {
                    "user_id": str(user.id),
                    "thread_id": str(thread_id),
                    "stream_id": stream_id,
                    "query": query,
                    "knowledge_type": knowledge_type,
                    "guardrail_flags": [],
                    "tenant_id": None,
                    "access_denied": False,
                    "denial_reason": None,
                    "knowledge_base_empty": False,
                    "condensed_query": "",
                    "original_condensed_query": "",
                    "retrieved_chunks": [],
                    "best_retrieved_chunks": [],
                    # These three gate the per-turn retrieve/reformulate
                    # retry loop (see assess_retrieval_sufficiency/
                    # _route_after_sufficiency_check in rag_graph.py) and
                    # MUST be reset here: the checkpointer persists the
                    # full graph state across turns in the same thread, so
                    # any key left out of this dict silently carries over
                    # the *previous* turn's final value instead of
                    # starting fresh -- without this, retrieval_attempts
                    # keeps climbing turn over turn (e.g. logging
                    # "attempt=4" on someone's first retry of their
                    # fourth message) until it permanently exceeds
                    # MAX_RETRIEVAL_ATTEMPTS, silently disabling retries
                    # for the rest of the conversation.
                    "retrieval_sufficient": False,
                    "retrieval_attempts": 0,
                    "last_retrieval_top_score": 0.0,
                    "response": "",
                    "citations": [],
                    "messages": [{"role": "user", "content": query}],
                }
                config = {
                    "configurable": {
                        "thread_id": str(thread_id),
                        "db": db,
                        "user": user,
                        "stream_id": stream_id,
                        "assistant_message_id": str(assistant_message_id),
                    },
                    # Surfaced on the LangSmith trace (when LangSmith
                    # tracing is enabled -- see .env's LANGCHAIN_TRACING/
                    # LANGSMITH_API_KEY) so a turn can be found/filtered
                    # by knowledge type, user, or thread without digging
                    # into node-level state. See the architecture doc's
                    # observability section.
                    "run_name": "agentic_rag_chat",
                    "tags": ["agentic-rag", knowledge_type],
                    "metadata": {
                        "user_id": str(user.id),
                        "knowledge_type": knowledge_type,
                        "thread_id": str(thread_id),
                    },
                }

                # durability="sync": each step's checkpoint is written before
                # the next step runs. The default ("async") writes them in the
                # background, so `persist` could mark the stream "completed"
                # before the checkpoint holding this turn's assistant reply
                # was saved -- and a quick follow-up message would then load
                # history without it.
                await _get_compiled_graph().ainvoke(
                    initial_state, config=config, durability="sync"
                )
        except asyncio.CancelledError:
            partial_content = "".join(
                chunk["content"]
                for chunk in stream_manager.get_buffer(stream_id)
                if chunk.get("type") == "token"
            )
            # A fresh session/connection, never the `db` above: cancellation
            # can land mid-await on that session's own connection (e.g. the
            # status="running" commit, or a query inside ainvoke), which can
            # leave it in a state that raises on any further use. Persisting
            # the final status must not depend on that connection having
            # survived the cancellation cleanly.
            try:
                async with async_session_factory() as recovery_db:
                    message = await recovery_db.get(ChatHistory, assistant_message_id)
                    if message is not None:
                        message.content = partial_content
                        message.status = "interrupted"
                        await recovery_db.commit()
            except Exception:
                logger.exception(
                    "Failed to persist interrupted status for stream_id=%s",
                    stream_id,
                )
            stream_manager.append_chunk(
                stream_id, {"type": "interrupted", "content": partial_content}
            )
            stream_manager.finish(stream_id)

            # Cancellation lands mid-`generate`, so that node's own
            # {"messages": [...]} return (which would normally record
            # what the assistant said) never happens -- the partial
            # answer would otherwise be visible only in Postgres
            # (assistant_message.content above), invisible to the
            # graph's own conversational memory. Recording it here
            # (best-effort; a continuable conversation matters more
            # than this succeeding) is what lets a later "continue"
            # naturally build on what was already said instead of the
            # agent having no idea a prior answer was in progress.
            if partial_content:
                try:
                    await _get_compiled_graph().aupdate_state(
                        {"configurable": {"thread_id": str(thread_id)}},
                        {
                            "messages": [
                                {
                                    "role": "assistant",
                                    "content": (
                                        f"{partial_content}\n\n"
                                        "[This response was interrupted "
                                        "before it finished.]"
                                    ),
                                }
                            ]
                        },
                    )
                except Exception:
                    logger.exception(
                        "Failed to record interrupted response into "
                        "checkpoint state for stream_id=%s",
                        stream_id,
                    )
        except Exception as exc:
            logger.exception("Chat turn failed for stream_id=%s", stream_id)
            user_message = _user_facing_error(exc)
            try:
                async with async_session_factory() as recovery_db:
                    message = await recovery_db.get(ChatHistory, assistant_message_id)
                    if message is not None:
                        message.status = "failed"
                        message.content = user_message
                        await recovery_db.commit()
            except Exception:
                logger.exception(
                    "Failed to persist failed status for stream_id=%s", stream_id
                )
            stream_manager.append_chunk(
                stream_id, {"type": "error", "content": user_message}
            )
            stream_manager.finish(stream_id)

    @staticmethod
    async def _enforce_no_concurrent_query(
        db: AsyncSession, *, user_id: uuid.UUID
    ) -> None:
        result = await db.execute(
            select(ChatHistory.id)
            .join(Thread, Thread.id == ChatHistory.thread_id)
            .where(
                Thread.user_id == user_id,
                ChatHistory.role == "assistant",
                ChatHistory.status.in_(_ACTIVE_STATUSES),
            )
        )
        if result.scalar_one_or_none() is not None:
            raise _ALREADY_RUNNING

    @staticmethod
    def cancel(stream_id: str) -> None:
        if not stream_manager.cancel_task(stream_id):
            raise _STREAM_NOT_FOUND

    @staticmethod
    async def get_status(db: AsyncSession, stream_id: str) -> ChatHistory:
        message = await db.get(ChatHistory, uuid.UUID(stream_id))
        if message is None:
            raise _STREAM_NOT_FOUND
        return message
