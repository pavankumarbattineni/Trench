"""In-process SSE fan-out + cancellation registry for chat streams.

One asyncio.Task runs a chat turn's generation in the background; any
number of SSE connections for the same stream_id can attach/reattach to
its buffered chunks (Last-Event-ID reconnect) via a fan-out queue.
Single-process only -- fine for one API worker; a multi-worker deployment
would need this backed by something shared (Redis pub/sub, Postgres
LISTEN/NOTIFY) instead, but the persisted ChatHistory row is always the
durable source of truth regardless.
"""

import asyncio
from dataclasses import dataclass, field


@dataclass
class _StreamState:
    buffer: list[dict] = field(default_factory=list)
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    task: asyncio.Task | None = None
    done: bool = False


class StreamManager:
    def __init__(self) -> None:
        self._streams: dict[str, _StreamState] = {}

    def _state(self, stream_id: str) -> _StreamState:
        return self._streams.setdefault(stream_id, _StreamState())

    def register_task(self, stream_id: str, task: asyncio.Task) -> None:
        self._state(stream_id).task = task

    def cancel_task(self, stream_id: str) -> bool:
        state = self._streams.get(stream_id)
        if state is None or state.task is None or state.task.done():
            return False
        state.task.cancel()
        return True

    def append_chunk(self, stream_id: str, chunk: dict) -> None:
        state = self._state(stream_id)
        state.buffer.append(chunk)
        for queue in state.subscribers:
            queue.put_nowait(chunk)

    def finish(self, stream_id: str) -> None:
        state = self._state(stream_id)
        state.done = True
        for queue in state.subscribers:
            queue.put_nowait(None)  # sentinel: no more chunks

    def subscribe(self, stream_id: str, *, from_index: int = 0) -> asyncio.Queue:
        state = self._state(stream_id)
        queue: asyncio.Queue = asyncio.Queue()
        for chunk in state.buffer[from_index:]:
            queue.put_nowait(chunk)
        if state.done:
            queue.put_nowait(None)
        else:
            state.subscribers.append(queue)
        return queue

    def unsubscribe(self, stream_id: str, queue: asyncio.Queue) -> None:
        state = self._streams.get(stream_id)
        if state is not None and queue in state.subscribers:
            state.subscribers.remove(queue)

    def buffer_length(self, stream_id: str) -> int:
        return len(self._state(stream_id).buffer)

    def get_buffer(self, stream_id: str) -> list[dict]:
        return list(self._state(stream_id).buffer)

    def is_done(self, stream_id: str) -> bool:
        state = self._streams.get(stream_id)
        return state is not None and state.done


stream_manager = StreamManager()
