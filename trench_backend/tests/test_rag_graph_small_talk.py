"""Conversational messages ("Thanks", "hi") skip the rewrite and search and
get a short natural reply -- instead of the model treating "Thanks" as a
request to restart the previous task from unrelated passages."""

from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.graph.rag_graph import (
    _BASE_SYSTEM_PROMPT,
    _route_after_access_check,
    generate,
    is_small_talk,
)


@pytest.mark.parametrize(
    "message",
    [
        "Thanks",
        "thanks!",
        "Thank you so much",
        "Thanks a lot 🙏",
        "hello there",
        "Great, got it!",
        "Good morning",
        "That was really helpful",
        "Noted, thanks",
    ],
)
def test_small_talk_is_recognised(message):
    assert is_small_talk(message)


@pytest.mark.parametrize(
    "message",
    [
        "ok",  # usually confirms an offer ("Shall I build it?")
        "yes",
        "sure",
        "Thanks, now do the same for my husband",
        "thanks but what about fat?",
        "Hi, what is my BMR?",
        "What is perfect competition?",
        "explain the second one",
    ],
)
def test_requests_are_not_small_talk(message):
    assert not is_small_talk(message)


def test_small_talk_skips_rewrite_and_search():
    state = {"access_denied": False, "knowledge_base_empty": False, "query": "Thanks!"}
    assert _route_after_access_check(state) == "generate"
    state["query"] = "Thanks, now compare the loans"
    assert _route_after_access_check(state) == "condense_query"


@pytest.mark.asyncio
async def test_small_talk_gets_the_short_reply_prompt_without_passages():
    captured = {}

    async def _capturing(_resolved, *, system_prompt, messages):
        captured.update(system_prompt=system_prompt, messages=messages)
        yield "You're welcome!"

    state = {
        "user_id": "u1",
        "thread_id": "t1",
        "stream_id": "s1",
        "query": "Thanks",
        "knowledge_type": "personal",
        "tenant_id": None,
        "knowledge_base_empty": False,
        "best_retrieved_chunks": [],
        "messages": [
            HumanMessage(content="Build my nutrition tool"),
            AIMessage(content="Here it is."),
            HumanMessage(content="Thanks"),
        ],
    }
    with (
        patch(
            "app.graph.rag_graph.LLMClientService.resolve_for_knowledge",
            AsyncMock(return_value="resolved-model"),
        ),
        patch("app.graph.rag_graph.LLMClientService.stream_generate", _capturing),
    ):
        result = await generate(
            state, {"configurable": {"db": object(), "user": object()}}
        )

    assert "conversational" in captured["system_prompt"]
    assert "restart" in captured["system_prompt"]
    assert captured["messages"][-1] == {"role": "user", "content": "Thanks"}
    assert result["response"] == "You're welcome!"


def test_answer_prompt_has_the_small_talk_backstop_rule():
    prompt = _BASE_SYSTEM_PROMPT.format(
        knowledge_scope_label="x", knowledge_scope_note=""
    )
    assert "9. If the user's latest message is only thanks" in prompt
