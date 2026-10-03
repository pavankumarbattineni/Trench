from app.service.chunking_service import CHUNK_SIZE, ChunkingService, count_tokens

_SAMPLE_TEXT = "\n\n".join(
    f"Paragraph {i} is about a different fruit and how it grows in a "
    "particular climate, with several sentences of detail to pad this "
    "out well past a single chunk's token budget on its own."
    for i in range(1, 40)
)


def test_chunk_splits_long_text_into_multiple_pieces():
    assert count_tokens(_SAMPLE_TEXT) > CHUNK_SIZE
    chunks = ChunkingService.chunk(_SAMPLE_TEXT)
    assert len(chunks) > 1
    assert all(chunk.strip() for chunk in chunks)


def test_chunk_returns_whole_text_when_short():
    chunks = ChunkingService.chunk("A short note.")
    assert chunks == ["A short note."]


def test_chunk_handles_markdown_structure():
    markdown = (
        "# Heading\n\nSome content under the heading.\n\n"
        "## Subheading\n\nMore content here that continues on for a while."
    )
    chunks = ChunkingService.chunk(markdown)
    assert chunks
    assert all(chunk.strip() for chunk in chunks)


def test_count_tokens_is_positive_for_nonempty_text():
    assert count_tokens("hello world") > 0
    assert count_tokens("") == 0
