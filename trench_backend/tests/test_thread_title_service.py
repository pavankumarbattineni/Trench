import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.database.models import Thread, User
from app.database.session import async_session_factory
from app.service.thread_title_service import ThreadTitleService

PREFIX = "test-thread-title-service"


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


async def _make_thread(session, suffix: str, *, title: str | None = None) -> Thread:
    user = User(email=f"{PREFIX}-{suffix}@example.com", username=f"ttl_{suffix}")
    session.add(user)
    await session.commit()
    await session.refresh(user)

    thread = Thread(user_id=user.id, title=title)
    session.add(thread)
    await session.commit()
    await session.refresh(thread)
    return thread


@pytest.mark.asyncio
async def test_generate_sets_title_from_first_message():
    async with async_session_factory() as session:
        thread = await _make_thread(session, "a")

        with patch(
            "app.service.thread_title_service.ThreadTitleService._complete",
            return_value="Notes about vacation policy",
        ):
            await ThreadTitleService.generate(
                session, thread_id=thread.id, query="What's our vacation policy?"
            )

        await session.refresh(thread)
        assert thread.title == "Notes about vacation policy"


@pytest.mark.asyncio
async def test_generate_does_not_overwrite_existing_title():
    async with async_session_factory() as session:
        thread = await _make_thread(session, "b", title="Already titled")

        with patch(
            "app.service.thread_title_service.ThreadTitleService._complete",
            return_value="A different title",
        ):
            await ThreadTitleService.generate(
                session, thread_id=thread.id, query="Something else"
            )

        await session.refresh(thread)
        assert thread.title == "Already titled"


@pytest.mark.asyncio
async def test_generate_swallows_completion_failure():
    async with async_session_factory() as session:
        thread = await _make_thread(session, "c")

        with patch(
            "app.service.thread_title_service.ThreadTitleService._complete",
            side_effect=RuntimeError("groq is down"),
        ):
            # Must not raise -- this runs as a fire-and-forget background
            # task that must never break the chat turn it's attached to.
            await ThreadTitleService.generate(
                session, thread_id=thread.id, query="Anything"
            )

        await session.refresh(thread)
        assert thread.title is None


@pytest.mark.asyncio
async def test_generate_is_a_noop_for_missing_thread():
    async with async_session_factory() as session:
        with patch(
            "app.service.thread_title_service.ThreadTitleService._complete",
            return_value="Some title",
        ):
            # Should return quietly rather than raise -- the thread may
            # have been deleted before the task got to it.
            await ThreadTitleService.generate(
                session, thread_id=uuid.uuid4(), query="Anything"
            )
