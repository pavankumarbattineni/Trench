"""Orchestrates a chat turn: persists the user message, spawns the LangGraph
run as a background asyncio task, and exposes status/cancel operations
against it.

Mirrors abyss_backend's stream lifecycle (background task + StreamManager
fan-out + status tracked in the persisted row + Task.cancel() for
interruption), scaled down to Trench's single-agent graph -- no tool
approval, no sub-agent dispatch.
"""

import asyncio
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
from app.utils.stream_manager import stream_manager

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
                ChatService._run_title_generation(thread_id=thread.id, query=query)
            )

        return assistant_message

    @staticmethod
    async def _run_title_generation(*, thread_id: uuid.UUID, query: str) -> None:
        async with async_session_factory() as db:
            await ThreadTitleService.generate(db, thread_id=thread_id, query=query)

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
        async with async_session_factory() as db:
            assistant_message = await db.get(ChatHistory, assistant_message_id)
            assistant_message.status = "running"
            await db.commit()

            initial_state = {
                "user_id": str(user.id),
                "thread_id": str(thread_id),
                "stream_id": stream_id,
                "query": query,
                "knowledge_type": knowledge_type,
                "organization_id": None,
                "access_denied": False,
                "denial_reason": None,
                "condensed_query": "",
                "retrieved_chunks": [],
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
                }
            }

            try:
                await _get_compiled_graph().ainvoke(initial_state, config=config)
            except asyncio.CancelledError:
                partial_content = "".join(
                    chunk["content"]
                    for chunk in stream_manager.get_buffer(stream_id)
                    if chunk.get("type") == "token"
                )
                assistant_message.content = partial_content
                assistant_message.status = "interrupted"
                await db.commit()
                stream_manager.append_chunk(
                    stream_id, {"type": "interrupted", "content": partial_content}
                )
                stream_manager.finish(stream_id)
            except Exception as exc:
                assistant_message.status = "failed"
                assistant_message.content = str(exc)
                await db.commit()
                stream_manager.append_chunk(
                    stream_id, {"type": "error", "content": str(exc)}
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
