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
        "best_retrieved_chunks": [_chunk("only", "Priya Sharma leads Project Falcon.")],
        "response": (
            "Priya Sharma leads Project Falcon [1]. She has led it since 2024 [1]."
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
async def test_build_citations_recognizes_full_width_cjk_brackets():
    """Despite _BASE_SYSTEM_PROMPT explicitly requiring ASCII "[n]" and
    forbidding full-width brackets, models have been observed to drift
    into "【n】" anyway -- these must still be recognized as real citation
    markers, or build_citations silently returns citations=[] (and the
    frontend's "N sources" panel never appears) even though the model did
    cite a real chunk."""
    state = {
        "best_retrieved_chunks": [_chunk("only", "Priya Sharma leads Project Falcon.")],
        "response": "Priya Sharma leads Project Falcon 【1】.",
    }

    result = await build_citations(state, {})

    assert [c["chunk_id"] for c in result["citations"]] == ["only"]
    assert result["citations"][0]["citation_number"] == 1


@pytest.mark.asyncio
async def test_build_citations_recognizes_mixed_ascii_and_full_width_markers():
    state = {
        "best_retrieved_chunks": [
            _chunk("first", "Priya Sharma leads Project Falcon."),
            _chunk("second", "The office has a rooftop garden."),
        ],
        "response": "Priya leads Falcon [1], and the office has a garden 【2】.",
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


# --- Interactive (```trench-ui) blocks ---------------------------------------


def _state(response: str, *, chunk_count: int) -> dict:
    return {
        "best_retrieved_chunks": [
            _chunk(f"c{i}", f"Passage {i}.") for i in range(1, chunk_count + 1)
        ],
        "response": response,
    }


@pytest.mark.asyncio
async def test_build_citations_reads_cite_arrays_in_interactive_blocks():
    """A chart may cite passages only through its JSON "cite" array (its text
    keeps [n] markers out of the JSON); those still become citations."""
    response = (
        "Your two loans compare like this [1].\n"
        "```trench-ui\n"
        '{"version":1,"components":[{"type":"bar_chart","title":"Rates",'
        '"x":["Home","Car"],"series":[{"name":"Rate","data":[8.4,9.1]}],'
        '"cite":[2,3]}]}\n'
        "```"
    )
    result = await build_citations(_state(response, chunk_count=3), {})
    assert [c["citation_number"] for c in result["citations"]] == [1, 2, 3]


@pytest.mark.asyncio
async def test_invalid_interactive_json_contributes_no_citations():
    response = 'Text [1].\n```trench-ui\n{"cite": [ 2 ], not json\n```'
    result = await build_citations(_state(response, chunk_count=3), {})
    assert [c["citation_number"] for c in result["citations"]] == [1]


def test_normal_answer_prompt_describes_interactive_blocks():
    from app.graph.rag_graph import _BASE_SYSTEM_PROMPT

    prompt = _BASE_SYSTEM_PROMPT.format(
        knowledge_scope_label="your documents",
        knowledge_scope_note="",
        context="[1] passage",
    )
    assert "trench-ui" in prompt
    assert '{"version":1,"components":[...]}' in prompt
    # The existing rules are untouched.
    assert "Cite every factual claim" in prompt
    assert "```mermaid" in prompt
