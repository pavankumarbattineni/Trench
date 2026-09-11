"""Chat: send a message (starts the LangGraph run), stream its response,
check its status, or interrupt it -- mirrors abyss_backend's
send-message/stream/status/cancel split.
"""

import uuid

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.database.session import get_db
from app.router.deps import get_current_user
from app.schemas.chat import (
    ChatMessageAcceptedResponse,
    ChatMessageRequest,
    StreamStatusResponse,
)
from app.service.chat_service import ChatService
from app.service.thread_service import ThreadService
from app.utils.sse import sse_generator

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post(
    "/threads/{thread_id}/messages",
    response_model=ChatMessageAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def send_message(
    thread_id: str,
    body: ChatMessageRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChatMessageAcceptedResponse:
    """Accepts a query and starts the LangGraph run in the background.

    Returns immediately (202 Accepted) with a stream_id the client can
    connect to for the streamed response, rather than blocking the
    request for the full duration of generation.

    Args:
        thread_id: The thread this message belongs to; must be owned by
            the authenticated user.
        body: The user's query and which knowledge base to answer from
            ("personal" or "company").
        current_user: The authenticated user sending the message.
        db: An active async SQLAlchemy session.

    Returns:
        The message's stream_id (used for /chat/streams/{stream_id} and
        friends -- the assistant message's own id, doubling as its stream
        id) and its initial status.

    Raises:
        HTTPException: 404 if the thread doesn't exist or isn't owned by
            the caller; 409 if the caller already has a query
            pending/running in any of their threads.
    """
    thread = await ThreadService.get_owned(
        db, user_id=current_user.id, thread_id=uuid.UUID(thread_id)
    )
    assistant_message = await ChatService.send_message(
        db,
        user=current_user,
        thread=thread,
        query=body.query,
        knowledge_type=body.knowledge_type,
    )
    return ChatMessageAcceptedResponse(
        stream_id=str(assistant_message.id),
        status=assistant_message.status,
    )


@router.get("/streams/{stream_id}")
async def stream(
    stream_id: str,
    request: Request,
    _current_user: User = Depends(get_current_user),
):
    """Streams the assistant's response as Server-Sent Events.

    Reconnects resume from where they left off via the standard SSE
    `Last-Event-ID` header rather than restarting the generation.

    Args:
        stream_id: The stream to connect to (the assistant message's id,
            as returned by POST /chat/threads/{thread_id}/messages).
        request: The incoming request (read for its Last-Event-ID header).
        _current_user: The authenticated user (only used to require auth).

    Returns:
        A `text/event-stream` response emitting `token`, `done`, `error`,
        and `interrupted` events as the underlying generation progresses.
    """
    last_event_id_header = request.headers.get("last-event-id")
    last_event_id = int(last_event_id_header) if last_event_id_header else None
    return StreamingResponse(
        sse_generator(stream_id, last_event_id=last_event_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.delete("/streams/{stream_id}", status_code=status.HTTP_204_NO_CONTENT)
async def interrupt_stream(
    stream_id: str, _current_user: User = Depends(get_current_user)
) -> None:
    """Interrupts/cancels an in-progress generation.

    Args:
        stream_id: The stream to interrupt.
        _current_user: The authenticated user (only used to require auth).

    Raises:
        HTTPException: 404 if the stream doesn't exist or has already
            finished (nothing left to cancel).
    """
    ChatService.cancel(stream_id)


@router.get("/streams/{stream_id}/status", response_model=StreamStatusResponse)
async def stream_status(
    stream_id: str,
    _current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamStatusResponse:
    """Returns the current state of a query.

    Args:
        stream_id: The stream to check.
        _current_user: The authenticated user (only used to require auth).
        db: An active async SQLAlchemy session.

    Returns:
        The current status ("pending" | "running" | "completed" |
        "failed" | "interrupted"), the response content so far, and any
        retrieved-chunk/citation metadata.

    Raises:
        HTTPException: 404 if the stream doesn't exist.
    """
    message = await ChatService.get_status(db, stream_id)
    return StreamStatusResponse(
        status=message.status, content=message.content, chunks=message.chunks
    )
