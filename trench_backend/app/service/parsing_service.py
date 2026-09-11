"""Extracts plain text from an uploaded document's raw bytes.

PDF/DOCX go through LlamaParse (structure/layout-aware extraction --
tables, multi-column text, scanned-page OCR -- worth the external API
round-trip). Plain text/markdown files are already text, so they're read
directly rather than sent through LlamaParse for no benefit.
"""

import asyncio
import selectors
from functools import lru_cache

import nest_asyncio
from llama_cloud_services import LlamaParse

from app.config import get_settings

_EXTENSION_BY_MIME = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}


@lru_cache
def _llamaparse_client() -> LlamaParse:
    api_key = get_settings().TRENCH_CONFIG.LLAMAPARSE.api_key
    return LlamaParse(api_key=api_key, result_type="markdown")


def _parse_sync(content: bytes, extension: str) -> str:
    """Runs LlamaParse's async client to completion on a dedicated,
    nest_asyncio-patched event loop.

    LlamaParse's own internals (via llama-index-core) synchronously drive
    a *second*, nested event loop even from its documented async entry
    points -- a quirk independent of our own caller. nest_asyncio patches
    a loop to tolerate that re-entrancy, but it can't patch uvloop (which
    uvicorn installs as the *process-wide* event loop policy, so even
    `asyncio.new_event_loop()` in a fresh worker thread would still hand
    back a uvloop instance). Constructing `asyncio.SelectorEventLoop`
    directly bypasses that policy and gets a loop class nest_asyncio can
    actually patch; running it inside its own worker thread (see
    `asyncio.to_thread` in `parse()` below) keeps it from ever touching
    uvicorn's own loop in the main thread.
    """
    loop = asyncio.SelectorEventLoop(selectors.DefaultSelector())
    nest_asyncio.apply(loop)
    # `result.get_text()` below is a sync method that itself internally
    # runs a further nested coroutine via `asyncio.get_event_loop()` --
    # without setting this as the thread's current loop, that call would
    # fall back to the (unpatchable) uvloop policy instead of reusing our
    # patched loop.
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(
            _llamaparse_client().aparse(
                content, extra_info={"file_name": f"upload.{extension}"}
            )
        )
        return result.get_text()
    finally:
        asyncio.set_event_loop(None)
        loop.close()


class ParsingService:
    @staticmethod
    async def parse(content: bytes, mime_type: str, *, filename: str) -> str:
        extension = _EXTENSION_BY_MIME.get(mime_type)
        if extension is None:
            # text/plain, text/markdown -- validate_upload already confirmed
            # these decode as UTF-8.
            return content.decode("utf-8")

        return await asyncio.to_thread(_parse_sync, content, extension)
