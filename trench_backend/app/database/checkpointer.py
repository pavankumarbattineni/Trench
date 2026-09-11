"""LangGraph's own Postgres-backed checkpointer -- persists graph state
(messages, in-progress node outputs) per thread_id so a conversation can
resume, stream, and be interrupted across requests/worker restarts.

Uses its own psycopg connection pool, separate from the app's SQLAlchemy
engine -- LangGraph's checkpoint tables are managed by its own `.setup()`
migration, not Alembic.
"""

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from app.config import get_settings

CHECKPOINTER_POOL_SIZE = 10

_pool: AsyncConnectionPool | None = None
_checkpointer: AsyncPostgresSaver | None = None


def _conninfo() -> str:
    db = get_settings().TRENCH_CONFIG.DB
    return f"postgresql://{db.username}:{db.password}@{db.ip_address}:{db.port}/{db.database}"


async def init_checkpointer() -> AsyncPostgresSaver:
    """Opens the checkpointer's connection pool and ensures its schema
    exists. Call once at app startup; idempotent to call again."""
    global _pool, _checkpointer
    if _checkpointer is not None:
        return _checkpointer

    _pool = AsyncConnectionPool(
        conninfo=_conninfo(),
        max_size=CHECKPOINTER_POOL_SIZE,
        kwargs={"autocommit": True, "prepare_threshold": 0},
        open=False,
    )
    await _pool.open()
    _checkpointer = AsyncPostgresSaver(_pool)
    await _checkpointer.setup()
    return _checkpointer


async def close_checkpointer() -> None:
    global _pool, _checkpointer
    if _pool is not None:
        await _pool.close()
    _pool = None
    _checkpointer = None


def get_checkpointer() -> AsyncPostgresSaver:
    if _checkpointer is None:
        raise RuntimeError(
            "Checkpointer not initialized -- call init_checkpointer() first"
        )
    return _checkpointer


async def delete_thread_checkpoints(thread_id: str) -> None:
    """Purges all of a thread's LangGraph checkpoint state (called when a
    Thread is deleted)."""
    if _pool is None:
        return
    async with _pool.connection() as conn:
        for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            await conn.execute(
                f"DELETE FROM {table} WHERE thread_id = %s",
                (thread_id,),  # noqa: S608
            )
