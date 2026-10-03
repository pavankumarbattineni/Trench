"""Business logic for conversation threads."""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.checkpointer import delete_thread_checkpoints
from app.database.models import ChatHistory, Thread

_NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")


class ThreadService:
    @staticmethod
    async def create(
        db: AsyncSession, *, user_id: uuid.UUID, title: str | None = None
    ) -> Thread:
        thread = Thread(user_id=user_id, title=title)
        db.add(thread)
        await db.commit()
        await db.refresh(thread)
        return thread

    @staticmethod
    async def list_for_user(db: AsyncSession, user_id: uuid.UUID) -> list[Thread]:
        result = await db.execute(
            select(Thread)
            .where(Thread.user_id == user_id)
            .order_by(Thread.updated_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_owned(
        db: AsyncSession, *, user_id: uuid.UUID, thread_id: uuid.UUID
    ) -> Thread:
        result = await db.execute(
            select(Thread).where(Thread.id == thread_id, Thread.user_id == user_id)
        )
        thread = result.scalar_one_or_none()
        if thread is None:
            raise _NOT_FOUND
        return thread

    @staticmethod
    async def get_messages(db: AsyncSession, thread_id: uuid.UUID) -> list[ChatHistory]:
        result = await db.execute(
            select(ChatHistory)
            .where(ChatHistory.thread_id == thread_id)
            .order_by(ChatHistory.created_at.asc())
        )
        return list(result.scalars().all())

    @classmethod
    async def update_title(
        cls, db: AsyncSession, *, user_id: uuid.UUID, thread_id: uuid.UUID, title: str
    ) -> Thread:
        """Renames a thread, overriding any auto-generated title.

        Raises:
            HTTPException: 404 if the thread doesn't exist or isn't owned
                by the caller.
        """
        thread = await cls.get_owned(db, user_id=user_id, thread_id=thread_id)
        thread.title = title
        await db.commit()
        await db.refresh(thread)
        return thread

    @staticmethod
    async def has_any_messages(db: AsyncSession, thread_id: uuid.UUID) -> bool:
        """Used to detect "this is a new thread's first message" (the
        trigger for auto title generation) without fetching every row."""
        result = await db.execute(
            select(ChatHistory.id).where(ChatHistory.thread_id == thread_id).limit(1)
        )
        return result.scalar_one_or_none() is not None

    @classmethod
    async def delete(
        cls, db: AsyncSession, *, user_id: uuid.UUID, thread_id: uuid.UUID
    ) -> None:
        thread = await cls.get_owned(db, user_id=user_id, thread_id=thread_id)
        await db.delete(thread)
        await db.commit()
        await delete_thread_checkpoints(str(thread_id))
