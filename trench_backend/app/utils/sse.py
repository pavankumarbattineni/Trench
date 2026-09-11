"""Formats StreamManager chunks as Server-Sent Events, with Last-Event-ID
reconnect support."""

import asyncio
import json
from collections.abc import AsyncIterator

from app.utils.stream_manager import stream_manager


def _format_event(index: int, chunk: dict) -> str:
    event_type = chunk.get("type", "token")
    data = json.dumps({k: v for k, v in chunk.items() if k != "type"})
    return f"id: {index}\nevent: {event_type}\ndata: {data}\n\n"


async def sse_generator(
    stream_id: str, *, last_event_id: int | None = None
) -> AsyncIterator[str]:
    from_index = (last_event_id + 1) if last_event_id is not None else 0
    queue = stream_manager.subscribe(stream_id, from_index=from_index)
    index = from_index
    try:
        while True:
            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=15)
            except TimeoutError:
                yield ": heartbeat\n\n"
                continue
            if chunk is None:
                break
            yield _format_event(index, chunk)
            index += 1
    finally:
        stream_manager.unsubscribe(stream_id, queue)
