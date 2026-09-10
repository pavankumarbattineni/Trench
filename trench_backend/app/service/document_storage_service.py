"""Where uploaded documents' raw bytes actually live.

Local filesystem for now -- no external signup, no cost, no card required.
A cloud provider (Supabase Storage, or Firebase Storage once its billing
policy is sorted) can be added later as a second implementation of this
same interface; nothing that calls DocumentStorageProvider needs to change.
"""

import asyncio
from pathlib import Path
from typing import Protocol

from app.config import get_settings


class DocumentStorageProvider(Protocol):
    async def save(self, path: str, content: bytes) -> None: ...
    async def read(self, path: str) -> bytes: ...
    async def delete(self, path: str) -> None: ...


class LocalFilesystemStorageProvider:
    """Stores files under a root directory, one subpath per document.

    All disk I/O is offloaded to a thread -- blocking file operations would
    otherwise stall the event loop, the same reason the DB layer is async
    throughout this app.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    async def save(self, path: str, content: bytes) -> None:
        full_path = self._root / path
        await asyncio.to_thread(full_path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(full_path.write_bytes, content)

    async def read(self, path: str) -> bytes:
        return await asyncio.to_thread((self._root / path).read_bytes)

    async def delete(self, path: str) -> None:
        full_path = self._root / path
        await asyncio.to_thread(full_path.unlink, missing_ok=True)


def get_storage_provider() -> DocumentStorageProvider:
    root = Path(get_settings().TRENCH_CONFIG.STORAGE.local_root_path)
    return LocalFilesystemStorageProvider(root)
