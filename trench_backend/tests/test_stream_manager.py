import asyncio

from app.utils.stream_manager import StreamManager


async def test_finish_evicts_the_stream_after_the_configured_delay():
    manager = StreamManager(eviction_delay_seconds=0.01)
    manager.append_chunk("s1", {"type": "token", "content": "hi"})

    manager.finish("s1")
    assert manager.is_done("s1")
    assert manager.buffer_length("s1") == 1

    await asyncio.sleep(0.05)

    # A fresh call re-creates empty state rather than reusing stale data --
    # this is how we observe eviction happened, since StreamManager has no
    # direct "does this key exist" accessor.
    assert manager.buffer_length("s1") == 0
    assert not manager.is_done("s1")


async def test_an_unfinished_stream_is_never_evicted():
    manager = StreamManager(eviction_delay_seconds=0.01)
    manager.append_chunk("s2", {"type": "token", "content": "hi"})

    await asyncio.sleep(0.05)

    assert manager.buffer_length("s2") == 1
    assert not manager.is_done("s2")
