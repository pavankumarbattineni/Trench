"""Conversation thread CRUD."""

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Thread, User
from app.database.session import get_db
from app.router.deps import get_current_user
from app.schemas.thread import (
    ThreadDetailResponse,
    ThreadListResponse,
    ThreadResponse,
    UpdateThreadTitleRequest,
)
from app.service.thread_service import ThreadService

router = APIRouter(prefix="/threads", tags=["threads"])


@router.post("", response_model=ThreadResponse)
async def create_thread(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Thread:
    """Creates a new, empty conversation thread for the authenticated user.

    Args:
        current_user: The authenticated user who will own the thread.
        db: An active async SQLAlchemy session.

    Returns:
        The newly created thread.
    """
    return await ThreadService.create(db, user_id=current_user.id)


@router.get("", response_model=ThreadListResponse)
async def list_threads(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ThreadListResponse:
    """Lists the user's threads, most recently updated first.

    Args:
        current_user: The authenticated user whose threads to list.
        db: An active async SQLAlchemy session.

    Returns:
        Every thread owned by the user, ordered by `updated_at` descending.
    """
    threads = await ThreadService.list_for_user(db, current_user.id)
    return ThreadListResponse(threads=threads)


@router.get("/{thread_id}", response_model=ThreadDetailResponse)
async def get_thread(
    thread_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ThreadDetailResponse:
    """Fetches a thread and its full chat history.

    Args:
        thread_id: The thread to fetch.
        current_user: The authenticated user; must own the thread.
        db: An active async SQLAlchemy session.

    Returns:
        The thread plus every chat_history message on it, oldest first.

    Raises:
        HTTPException: 404 if the thread doesn't exist or isn't owned by
            the requester.
    """
    thread = await ThreadService.get_owned(
        db, user_id=current_user.id, thread_id=thread_id
    )
    messages = await ThreadService.get_messages(db, thread_id)
    return ThreadDetailResponse(thread=thread, messages=messages)


@router.patch("/{thread_id}", response_model=ThreadResponse)
async def update_thread_title(
    thread_id: uuid.UUID,
    body: UpdateThreadTitleRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Thread:
    """Renames a thread, overriding any auto-generated title.

    Args:
        thread_id: The thread to rename.
        body: The new title.
        current_user: The authenticated user; must own the thread.
        db: An active async SQLAlchemy session.

    Returns:
        The updated thread.

    Raises:
        HTTPException: 404 if the thread doesn't exist or isn't owned by
            the requester.
    """
    return await ThreadService.update_title(
        db, user_id=current_user.id, thread_id=thread_id, title=body.title
    )


@router.delete("/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(
    thread_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Deletes a thread, its chat history, and its LangGraph checkpoints.

    Args:
        thread_id: The thread to delete.
        current_user: The authenticated user; must own the thread.
        db: An active async SQLAlchemy session.

    Raises:
        HTTPException: 404 if the thread doesn't exist or isn't owned by
            the requester.
    """
    await ThreadService.delete(db, user_id=current_user.id, thread_id=thread_id)
