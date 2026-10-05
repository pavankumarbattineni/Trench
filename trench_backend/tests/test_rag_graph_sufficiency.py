import pytest

from app.graph.rag_graph import (
    MAX_RETRIEVAL_ATTEMPTS,
    RETRIEVAL_SKIP_RETRY_BELOW,
    RETRIEVAL_SUFFICIENCY_MIN_RERANK_SCORE,
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
async def test_assess_sufficiency_marks_high_rerank_score_chunks_sufficient():
    state = {
        "retrieved_chunks": [_chunk(0.8)],
        "retrieval_attempts": 0,
        "best_retrieved_chunks": [],
    }

    result = await assess_retrieval_sufficiency(state, {})

    assert result["retrieval_sufficient"] is True
    assert result["retrieval_attempts"] == 1
    assert result["last_retrieval_top_score"] == 0.8


@pytest.mark.asyncio
async def test_assess_sufficiency_marks_low_rerank_score_chunks_insufficient():
    state = {
        "retrieved_chunks": [_chunk(0.1)],
        "retrieval_attempts": 0,
        "best_retrieved_chunks": [],
    }

    result = await assess_retrieval_sufficiency(state, {})

    assert result["retrieval_sufficient"] is False
    assert result["retrieval_attempts"] == 1


@pytest.mark.asyncio
async def test_assess_sufficiency_marks_no_chunks_insufficient():
    state = {
        "retrieved_chunks": [],
        "retrieval_attempts": 0,
        "best_retrieved_chunks": [],
    }

    result = await assess_retrieval_sufficiency(state, {})

    assert result["retrieval_sufficient"] is False
    assert result["last_retrieval_top_score"] == 0.0


@pytest.mark.asyncio
async def test_assess_sufficiency_increments_attempts_each_call():
    state = {
        "retrieved_chunks": [_chunk(0.1)],
        "retrieval_attempts": 1,
        "best_retrieved_chunks": [_chunk(0.1)],
    }

    result = await assess_retrieval_sufficiency(state, {})

    assert result["retrieval_attempts"] == 2


@pytest.mark.asyncio
async def test_assess_sufficiency_exactly_at_the_threshold_counts_as_sufficient():
    state = {
        "retrieved_chunks": [_chunk(RETRIEVAL_SUFFICIENCY_MIN_RERANK_SCORE)],
        "retrieval_attempts": 0,
        "best_retrieved_chunks": [],
    }

    result = await assess_retrieval_sufficiency(state, {})

    assert result["retrieval_sufficient"] is True


def test_routes_to_generate_when_sufficient():
    state = {
        "retrieval_sufficient": True,
        "retrieval_attempts": 1,
        "last_retrieval_top_score": 0.9,
    }
    assert _route_after_sufficiency_check(state) == "generate"


def test_routes_to_reformulate_when_insufficient_under_cap_and_above_skip_floor():
    state = {
        "retrieval_sufficient": False,
        "retrieval_attempts": 1,
        "last_retrieval_top_score": RETRIEVAL_SKIP_RETRY_BELOW,
    }
    assert _route_after_sufficiency_check(state) == "reformulate_query"


def test_routes_to_generate_once_retry_cap_is_reached_even_if_insufficient():
    """Hard cap: MAX_RETRIEVAL_ATTEMPTS total attempts -- after that,
    proceed with the best attempt seen so far rather than looping
    forever."""
    state = {
        "retrieval_sufficient": False,
        "retrieval_attempts": MAX_RETRIEVAL_ATTEMPTS,
        "last_retrieval_top_score": 0.2,
    }
    assert _route_after_sufficiency_check(state) == "generate"


def test_routes_to_generate_when_latest_attempt_is_below_the_skip_floor():
    """Below RETRIEVAL_SKIP_RETRY_BELOW the namespace almost certainly has
    nothing relevant -- reformulating and retrying is skipped even though
    the retry cap hasn't been reached yet."""
    state = {
        "retrieval_sufficient": False,
        "retrieval_attempts": 1,
        "last_retrieval_top_score": RETRIEVAL_SKIP_RETRY_BELOW - 0.01,
    }
    assert _route_after_sufficiency_check(state) == "generate"


def test_max_retrieval_attempts_is_two():
    assert MAX_RETRIEVAL_ATTEMPTS == 2


# --- best_retrieved_chunks tracking across the retry loop ---


@pytest.mark.asyncio
async def test_first_attempt_becomes_the_best_even_if_insufficient():
    state = {
        "retrieved_chunks": [_chunk(0.1)],
        "retrieval_attempts": 0,
        "best_retrieved_chunks": [],
    }

    result = await assess_retrieval_sufficiency(state, {})

    assert result["best_retrieved_chunks"] == [_chunk(0.1)]


@pytest.mark.asyncio
async def test_a_worse_second_attempt_does_not_replace_a_better_first_attempt():
    """Reformulating the query is an attempt to do better, not a
    guarantee -- a retry that scores worse than an earlier attempt must
    not discard the earlier, better context."""
    first_attempt_chunks = [_chunk(0.6)]
    state = {
        "retrieved_chunks": [_chunk(0.2)],  # this (second) attempt is worse
        "retrieval_attempts": 1,
        "best_retrieved_chunks": first_attempt_chunks,
    }

    result = await assess_retrieval_sufficiency(state, {})

    assert result["best_retrieved_chunks"] == first_attempt_chunks


@pytest.mark.asyncio
async def test_a_better_second_attempt_replaces_the_first():
    state = {
        "retrieved_chunks": [_chunk(0.9)],  # this (second) attempt is better
        "retrieval_attempts": 1,
        "best_retrieved_chunks": [_chunk(0.2)],
    }

    result = await assess_retrieval_sufficiency(state, {})

    assert result["best_retrieved_chunks"] == [_chunk(0.9)]
