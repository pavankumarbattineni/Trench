"""Splits parsed document text into chunks.

A single fixed strategy, chosen in code rather than exposed as
per-user/per-document configuration: split on Markdown headers first (so a
chunk never straddles two unrelated sections), then split each section with
the existing token-aware recursive splitter (paragraph/sentence/word
boundaries, preferring Markdown structure like headers and code fences
first) -- a solid general-purpose choice given LlamaParse already returns
Markdown for PDF/DOCX, and plain text/Markdown uploads are Markdown-
compatible as-is.

Each chunk also carries its heading_path (e.g. "Refunds > Timelines") --
empty for TXT uploads or any section with no headers above it -- so
DocumentIngestionService can prefix the document name and heading path onto
the text that actually gets embedded/sparse-encoded, giving the model more
context than the raw chunk text alone (the chunk's own `text` stored as
Pinecone metadata stays the raw, unprefixed text).
"""

from dataclasses import dataclass

import tiktoken
from langchain_text_splitters import MarkdownHeaderTextSplitter, MarkdownTextSplitter

_ENCODING = tiktoken.get_encoding("cl100k_base")

CHUNK_SIZE = 512
CHUNK_OVERLAP = 80

_HEADERS_TO_SPLIT_ON = [
    ("#", "h1"),
    ("##", "h2"),
    ("###", "h3"),
    ("####", "h4"),
]
_HEADER_NAMES = [name for _, name in _HEADERS_TO_SPLIT_ON]


def count_tokens(text: str) -> int:
    return len(_ENCODING.encode(text))


@dataclass
class Chunk:
    text: str
    heading_path: str


class ChunkingService:
    @staticmethod
    def chunk(content: str) -> list[Chunk]:
        header_splitter = MarkdownHeaderTextSplitter(
            _HEADERS_TO_SPLIT_ON, strip_headers=False
        )
        sections = header_splitter.split_text(content)

        text_splitter = MarkdownTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=CHUNK_OVERLAP,
            length_function=count_tokens,
        )

        chunks = []
        for section in sections:
            heading_path = " > ".join(
                section.metadata[name]
                for name in _HEADER_NAMES
                if name in section.metadata
            )
            for piece in text_splitter.split_text(section.page_content):
                if piece.strip():
                    chunks.append(Chunk(text=piece, heading_path=heading_path))
        return chunks
