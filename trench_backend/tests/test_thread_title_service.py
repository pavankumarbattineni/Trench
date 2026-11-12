import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.database.models import Thread, User
from app.database.session import async_session_factory
from app.service.thread_title_service import (
    ThreadTitleService,
    clean_title,
    fallback_title,
)

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
        assert thread.title == "Notes About Vacation Policy"


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


# --- The model answering instead of titling ---------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "I’m sorry, but I can’t help with that.",
        "I'm happy to help you analyze your home-loan repayment, "
        "but I'll need some details.",
        "Sure! Here's a title",
        "What would you like to know?",
        "A title that is far too long to be a title here",
        "",
    ],
)
def test_replies_and_long_text_are_not_titles(raw):
    assert clean_title(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('"home loan repayment analysis."', "Home Loan Repayment Analysis"),
        ("Title: EMI vs SIP comparison", "EMI vs SIP Comparison"),
        ("**Request for assistance**", "Request for Assistance"),
    ],
)
def test_titles_are_cleaned_and_title_cased(raw, expected):
    assert clean_title(raw) == expected


def test_fallback_title_uses_the_messages_key_words():
    query = (
        "I want to really understand how my home loan EMIs are calculated "
        "and what happens if I prepay"
    )
    title = fallback_title(query)
    assert title == "Home Loan EMIs Calculated"
    assert len(title.split()) <= 6
    assert fallback_title("Can you help me?") == "Request for Assistance"


@pytest.mark.asyncio
async def test_a_refusal_is_retried_then_replaced_by_a_key_word_title():
    async with async_session_factory() as session:
        thread = await _make_thread(session, "d")
        calls = []

        async def refusing(db, query, *, retry=False):
            calls.append(retry)
            return "I’m sorry, but I can’t help with that."

        with patch(
            "app.service.thread_title_service.ThreadTitleService._complete",
            refusing,
        ):
            await ThreadTitleService.generate(
                session,
                thread_id=thread.id,
                query="Plan my weekly groceries on a budget",
            )

        await session.refresh(thread)
        assert calls == [False, True]
        assert thread.title == "Plan Weekly Groceries Budget"


@pytest.mark.asyncio
async def test_a_good_retry_is_used():
    async with async_session_factory() as session:
        thread = await _make_thread(session, "e")
        replies = iter(["Sure, I can help with that!", "Grocery Budget Planning"])

        async def complete(db, query, *, retry=False):
            return next(replies)

        with patch(
            "app.service.thread_title_service.ThreadTitleService._complete",
            complete,
        ):
            await ThreadTitleService.generate(
                session, thread_id=thread.id, query="Plan my weekly groceries"
            )

        await session.refresh(thread)
        assert thread.title == "Grocery Budget Planning"
