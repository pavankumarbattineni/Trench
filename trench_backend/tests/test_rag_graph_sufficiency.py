import pytest

from app.graph.rag_graph import (
    MAX_RETRIEVAL_ATTEMPTS,
    _route_after_sufficiency_check,
    assess_retrieval_sufficiency,
)


def _chunk(score: float) -> dict:
    return {
        "chunk_id": "c1",
        "document_id": "d1",
        "document_name": "doc.txt",
        "chunk_index": 0,
        "content": "some text",
        "score": score,
    }


@pytest.mark.asyncio
async def test_assess_sufficiency_marks_high_score_chunks_sufficient():
    state = {"retrieved_chunks": [_chunk(0.8)], "retrieval_attempts": 0}

    result = await assess_retrieval_sufficiency(state, {})

    assert result["retrieval_sufficient"] is True
    assert result["retrieval_attempts"] == 1


@pytest.mark.asyncio
async def test_assess_sufficiency_marks_low_score_chunks_insufficient():
    state = {"retrieved_chunks": [_chunk(0.1)], "retrieval_attempts": 0}

    result = await assess_retrieval_sufficiency(state, {})

    assert result["retrieval_sufficient"] is False
    assert result["retrieval_attempts"] == 1


@pytest.mark.asyncio
async def test_assess_sufficiency_marks_no_chunks_insufficient():
    state = {"retrieved_chunks": [], "retrieval_attempts": 0}

    result = await assess_retrieval_sufficiency(state, {})

    assert result["retrieval_sufficient"] is False


@pytest.mark.asyncio
async def test_assess_sufficiency_increments_attempts_each_call():
    state = {"retrieved_chunks": [_chunk(0.1)], "retrieval_attempts": 1}

    result = await assess_retrieval_sufficiency(state, {})

    assert result["retrieval_attempts"] == 2


def test_routes_to_generate_when_sufficient():
    state = {"retrieval_sufficient": True, "retrieval_attempts": 1}
    assert _route_after_sufficiency_check(state) == "generate"


def test_routes_to_reformulate_when_insufficient_and_under_cap():
    state = {"retrieval_sufficient": False, "retrieval_attempts": 1}
    assert _route_after_sufficiency_check(state) == "reformulate_query"


def test_routes_to_generate_once_retry_cap_is_reached_even_if_insufficient():
    """Hard cap: 2 retries after the initial attempt, 3 total -- after
    that, proceed with the best available context rather than looping
    forever (see architecture doc §10)."""
    state = {
        "retrieval_sufficient": False,
        "retrieval_attempts": MAX_RETRIEVAL_ATTEMPTS,
    }
    assert _route_after_sufficiency_check(state) == "generate"


def test_max_retrieval_attempts_is_three():
    assert MAX_RETRIEVAL_ATTEMPTS == 3
