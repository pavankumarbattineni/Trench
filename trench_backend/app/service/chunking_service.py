"""Splits parsed document text into chunks.

A single fixed strategy, chosen in code rather than exposed as
per-user/per-document configuration: Markdown-aware recursive splitting
(paragraph/sentence/word boundaries, preferring Markdown structure like
headers and code fences first) -- a solid general-purpose choice given
LlamaParse already returns Markdown for PDF/DOCX, and plain text/Markdown
uploads are Markdown-compatible as-is.
"""

import tiktoken
from langchain_text_splitters import MarkdownTextSplitter

_ENCODING = tiktoken.get_encoding("cl100k_base")

CHUNK_SIZE = 512
CHUNK_OVERLAP = 80


def count_tokens(text: str) -> int:
    return len(_ENCODING.encode(text))


class ChunkingService:
    @staticmethod
    def chunk(content: str) -> list[str]:
        splitter = MarkdownTextSplitter(
            chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
        )
        return [chunk for chunk in splitter.split_text(content) if chunk.strip()]
