from unittest.mock import patch

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
    state = {"retrieved_chunks": [], "response": "I don't have enough information."}

    result = await build_citations(state, {})

    assert result["citations"] == []


@pytest.mark.asyncio
async def test_stub_mode_keeps_every_retrieved_chunk_regardless_of_word_overlap():
    """Without real JEV configured (the only mode this project currently
    runs in -- TYPESAFE.api_key is unset), the word-overlap stub isn't
    filtered on at all: it's unreliable specifically for answers that
    *compute* something from the source (e.g. "8 x 64 = 512") rather than
    quoting it, which scores low word-overlap despite being the genuine
    source -- confirmed to silently drop real citations for that whole
    class of question. Every retrieved chunk is kept instead, relying on
    hybrid retrieval + Cohere rerank (already run before this node) for
    relevance."""
    state = {
        "retrieved_chunks": [
            _chunk("a", "Priya Sharma is the project lead for Falcon."),
            _chunk("b", "Bananas are a good source of potassium."),
        ],
        "response": "The project lead for Project Falcon is Priya Sharma.",
    }

    with patch("app.graph.rag_graph.JevService.is_configured", return_value=False):
        result = await build_citations(state, {})

    citation_ids = [c["chunk_id"] for c in result["citations"]]
    assert citation_ids == ["a", "b"]
    assert [c["citation_number"] for c in result["citations"]] == [1, 2]


@pytest.mark.asyncio
async def test_configured_jev_still_filters_to_chunks_actually_used():
    """Once a real JEV judge is configured, its per-chunk relevance
    verdict is trusted and filtering applies as designed -- this is the
    one mode where dropping unused chunks is reliable enough to do."""
    state = {
        "retrieved_chunks": [
            _chunk("used", "Priya Sharma is the project lead for Falcon."),
            _chunk("unused", "Bananas are a good source of potassium."),
        ],
        "response": "The project lead for Project Falcon is Priya Sharma.",
    }

    async def _fake_ask_noul(question, *, stub_fallback):
        # Simulates a real JEV judge agreeing with the stub's own signal
        # on this clearly-distinguishable fixture (real overlap vs. none)
        # -- this test is about the filtering mechanism, not re-testing
        # the stub heuristic's own accuracy.
        return 1.0 if stub_fallback() >= 0.5 else 0.0

    with (
        patch("app.graph.rag_graph.JevService.is_configured", return_value=True),
        patch("app.graph.rag_graph.JevService.ask_noul", side_effect=_fake_ask_noul),
    ):
        result = await build_citations(state, {})

    citation_ids = {c["chunk_id"] for c in result["citations"]}
    assert citation_ids == {"used"}


@pytest.mark.asyncio
async def test_citation_number_matches_original_position_even_after_filtering():
    """citation_number must survive filtering as the chunk's position in
    the ORIGINAL retrieved_chunks list (what generate() told the model to
    cite as "[n]") -- not its position in the filtered survivors list,
    which shifts as soon as an earlier chunk is dropped. The frontend
    matches a clicked "[n]" marker to a chunk by this number, so a
    mismatch here means clicking a citation opens the wrong source.
    Exercised here with JEV configured, since stub mode no longer filters
    at all (see test_stub_mode_keeps_every_retrieved_chunk...)."""
    state = {
        "retrieved_chunks": [
            _chunk("dropped-first", "Bananas are a good source of potassium."),
            _chunk("kept-second", "Priya Sharma is the project lead for Falcon."),
            _chunk("dropped-third", "The office has a rooftop garden."),
        ],
        "response": "The project lead for Project Falcon is Priya Sharma.",
    }

    async def _fake_ask_noul(question, *, stub_fallback):
        return 1.0 if stub_fallback() >= 0.5 else 0.0

    with (
        patch("app.graph.rag_graph.JevService.is_configured", return_value=True),
        patch("app.graph.rag_graph.JevService.ask_noul", side_effect=_fake_ask_noul),
    ):
        result = await build_citations(state, {})

    assert len(result["citations"]) == 1
    kept = result["citations"][0]
    assert kept["chunk_id"] == "kept-second"
    # Position 2 in the original list, not position 1 in the filtered one.
    assert kept["citation_number"] == 2
