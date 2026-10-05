import pytest

from app.graph.rag_graph import build_citations


def _chunk(chunk_id: str, content: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "document_id": "d1",
        "document_name": "doc.txt",
        "chunk_index": 0,
        "content": content,
        "score": 0.9,
    }


@pytest.mark.asyncio
async def test_build_citations_returns_empty_list_for_no_retrieved_chunks():
    state = {
        "best_retrieved_chunks": [],
        "response": "I don't have enough information.",
    }

    result = await build_citations(state, {})

    assert result["citations"] == []


@pytest.mark.asyncio
async def test_build_citations_returns_empty_list_when_response_has_no_markers():
    state = {
        "best_retrieved_chunks": [_chunk("a", "Priya Sharma leads Project Falcon.")],
        "response": "I don't have enough information to answer that.",
    }

    result = await build_citations(state, {})

    assert result["citations"] == []


@pytest.mark.asyncio
async def test_build_citations_keeps_only_chunks_with_a_marker_in_the_response():
    state = {
        "best_retrieved_chunks": [
            _chunk("cited", "Priya Sharma leads Project Falcon."),
            _chunk("not-cited", "Bananas are a good source of potassium."),
        ],
        "response": "The project lead for Project Falcon is Priya Sharma [1].",
    }

    result = await build_citations(state, {})

    assert [c["chunk_id"] for c in result["citations"]] == ["cited"]
    assert result["citations"][0]["citation_number"] == 1


@pytest.mark.asyncio
async def test_build_citations_drops_an_out_of_range_hallucinated_marker():
    """A "[n]" marker outside 1..len(chunks) means the model cited a
    passage number nothing was ever put at -- dropped rather than raising
    or guessing which real chunk it meant."""
    state = {
        "best_retrieved_chunks": [_chunk("only", "Priya Sharma leads Project Falcon.")],
        "response": "Priya Sharma leads Project Falcon [1], per our records [7].",
    }

    result = await build_citations(state, {})

    assert [c["chunk_id"] for c in result["citations"]] == ["only"]


@pytest.mark.asyncio
async def test_build_citations_deduplicates_a_repeated_marker():
    state = {
        "best_retrieved_chunks": [
            _chunk("only", "Priya Sharma leads Project Falcon.")
        ],
        "response": (
            "Priya Sharma leads Project Falcon [1]. She has led it since "
            "2024 [1]."
        ),
    }

    result = await build_citations(state, {})

    assert len(result["citations"]) == 1
    assert result["citations"][0]["chunk_id"] == "only"


@pytest.mark.asyncio
async def test_build_citations_orders_by_marker_number_not_mention_order():
    state = {
        "best_retrieved_chunks": [
            _chunk("first", "Priya Sharma leads Project Falcon."),
            _chunk("second", "The office has a rooftop garden."),
        ],
        "response": "The office has a rooftop garden [2], and Priya leads Falcon [1].",
    }

    result = await build_citations(state, {})

    assert [c["citation_number"] for c in result["citations"]] == [1, 2]


@pytest.mark.asyncio
async def test_citation_number_matches_original_position_even_with_a_gap():
    """citation_number equals the chunk's 1-based position in
    best_retrieved_chunks -- the exact numbering generate() put in the
    prompt -- never the position among only the cited survivors, so a
    clicked "[2]" always opens the chunk generate() actually labeled [2]."""
    state = {
        "best_retrieved_chunks": [
            _chunk("not-cited-first", "Bananas are a good source of potassium."),
            _chunk("cited-second", "Priya Sharma leads Project Falcon."),
            _chunk("not-cited-third", "The office has a rooftop garden."),
        ],
        "response": "Priya Sharma leads Project Falcon [2].",
    }

    result = await build_citations(state, {})

    assert len(result["citations"]) == 1
    kept = result["citations"][0]
    assert kept["chunk_id"] == "cited-second"
    assert kept["citation_number"] == 2
