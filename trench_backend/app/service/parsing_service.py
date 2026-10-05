"""Extracts plain text from an uploaded document's raw bytes.

PDF/DOCX go through LlamaParse (structure/layout-aware extraction --
tables, multi-column text, scanned-page OCR -- worth the external API
round-trip). Plain text/markdown files are already text, so they're read
directly rather than sent through LlamaParse for no benefit.
"""

import asyncio
import selectors

import nest_asyncio
from llama_cloud_services import LlamaParse

from app.config import get_settings

_EXTENSION_BY_MIME = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}


def _new_llamaparse_client() -> LlamaParse:
    api_key = get_settings().TRENCH_CONFIG.LLAMAPARSE.api_key
    return LlamaParse(api_key=api_key, result_type="markdown")


async def _parse_async(content: bytes, extension: str) -> str:
    """Builds a fresh LlamaParse client for this call and closes its
    underlying httpx connection before returning.

    LlamaParse lazily opens its own httpx.AsyncClient the first time it's
    used, bound to whichever event loop is running at that moment (see
    llama_cloud_services.parse.base.BaseAsyncParse.aclient). A single
    *cached* client (the previous design, via @lru_cache) would therefore
    end up holding a connection tied to this call's loop even after
    _parse_sync's own `finally` closes that loop -- the next call, with a
    fresh loop, would reuse the same cached client, and the moment httpx
    tried to clean up that leftover connection it would call back into
    the long-dead first loop and raise "RuntimeError: Event loop is
    closed". A new client per call, explicitly closed here before this
    loop closes, keeps every connection's lifetime scoped to exactly the
    one loop that created it.
    """
    client = _new_llamaparse_client()
    try:
        result = await client.aparse(
            content, extra_info={"file_name": f"upload.{extension}"}
        )
        # result.aget_text(), not the sync result.get_text() -- get_text()
        # calls llama_index's asyncio_run(), which (correctly) detects
        # we're already inside a running loop here and "solves" that by
        # spawning a brand new thread with a brand new, unpatched loop to
        # run aget_text() on instead. That inner call still reuses this
        # same client's httpx.AsyncClient/anyio primitives, which were
        # created on *this* loop/thread -- using them from the spawned
        # thread's different loop raises "bound to a different event
        # loop" (or, worse, intermittently just hangs/half-fails,
        # depending on timing). Awaiting aget_text() directly keeps
        # everything on this one loop and thread, where it belongs.
        return await result.aget_text()
    finally:
        await client.aclient.aclose()


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
    # Some of LlamaParse's internals still fall back to
    # `asyncio.get_event_loop()` rather than using whatever loop is
    # actually driving the current call -- setting this as the thread's
    # current loop makes sure that falls back to our patched loop instead
    # of the (unpatchable) uvloop policy uvicorn installs process-wide.
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_parse_async(content, extension))
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
