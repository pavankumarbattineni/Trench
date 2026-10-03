"""Where uploaded documents' raw bytes actually live.

Production storage is a Backblaze B2 bucket reached through B2's
S3-compatible API with boto3 (B2StorageProvider). The local-filesystem
implementation is kept as a second implementation of the same interface
for tests -- they patch get_storage_provider() to return it -- but it is
never the default any more: the app must not depend on local disk for
document bytes, since that breaks as soon as there's more than one
instance or the container is replaced.
"""

import asyncio
import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import quote

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.config import BackblazeConfig, get_settings

logger = logging.getLogger(__name__)

Disposition = Literal["inline", "attachment"]


class DocumentStorageError(Exception):
    """A storage backend operation failed (network, auth, missing bucket,
    ...). The single exception callers need to catch -- boto3/botocore
    exception types never escape this module."""


class DocumentStorageProvider(Protocol):
    async def save(self, path: str, content: bytes) -> None: ...
    async def read(self, path: str) -> bytes: ...
    async def delete(self, path: str) -> None: ...
    async def generate_presigned_url(
        self,
        path: str,
        *,
        filename: str,
        mime_type: str,
        disposition: Disposition,
        expires_in: int = 300,
    ) -> str: ...


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

    async def generate_presigned_url(
        self,
        path: str,
        *,
        filename: str,
        mime_type: str,
        disposition: Disposition,
        expires_in: int = 300,
    ) -> str:
        # There's no server to presign against for bytes on local disk, and
        # this provider is only ever used by tests that don't exercise
        # download -- production always goes through B2StorageProvider.
        raise NotImplementedError(
            "LocalFilesystemStorageProvider cannot generate download URLs"
        )


def _content_disposition(disposition: Disposition, filename: str) -> str:
    """Builds a Content-Disposition value safe to embed in a header.

    The plain `filename="..."` parameter must be quoted ASCII, so quotes,
    backslashes and non-ASCII characters are replaced there; the RFC 5987
    `filename*` parameter carries the exact UTF-8 name for every modern
    browser, which prefers it when present.
    """
    ascii_fallback = "".join(
        char if char.isascii() and char not in '"\\' and char.isprintable() else "_"
        for char in filename
    )
    return (
        f'{disposition}; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )


class B2StorageProvider:
    """Stores files as objects in a Backblaze B2 bucket via its
    S3-compatible API, keyed by the same `{knowledge_type}/{owner_scope}/
    {document_id}/{filename}` path the rest of the app already uses.

    boto3 is synchronous, so every call is offloaded with
    asyncio.to_thread (boto3 clients, unlike sessions, are thread-safe).
    Every botocore failure is logged with its original detail and then
    re-raised as DocumentStorageError, so callers handle exactly one
    exception type and never depend on boto3 internals.
    """

    def __init__(self, config: BackblazeConfig) -> None:
        self._bucket = config.bucket_name
        self._client = boto3.client(
            "s3",
            endpoint_url=config.endpoint_url,
            region_name=config.region,
            aws_access_key_id=config.application_key_id,
            aws_secret_access_key=config.application_key,
        )

    async def save(self, path: str, content: bytes) -> None:
        try:
            await asyncio.to_thread(
                self._client.put_object, Bucket=self._bucket, Key=path, Body=content
            )
        except (BotoCoreError, ClientError) as exc:
            logger.error("B2 put_object failed | key=%s error=%r", path, exc)
            raise DocumentStorageError(
                "Failed to write the document to storage."
            ) from exc

    async def read(self, path: str) -> bytes:
        def _read() -> bytes:
            # The streaming body read is network I/O too, so it belongs in
            # the worker thread alongside get_object itself.
            response = self._client.get_object(Bucket=self._bucket, Key=path)
            return response["Body"].read()

        try:
            return await asyncio.to_thread(_read)
        except (BotoCoreError, ClientError) as exc:
            logger.error("B2 get_object failed | key=%s error=%r", path, exc)
            raise DocumentStorageError(
                "Failed to read the document from storage."
            ) from exc

    async def delete(self, path: str) -> None:
        # The bucket has versioning enabled (B2's default), so a plain
        # DeleteObject without a VersionId only writes a delete marker --
        # the prior version's bytes stay in the bucket (and keep being
        # billed) until that version is deleted explicitly. Document
        # deletion is meant to be final, so every version of this key is
        # listed and deleted by (key, VersionId) rather than relying on a
        # single unversioned delete. S3 DeleteObject(s) are idempotent:
        # deleting a key/version that doesn't exist succeeds, so no
        # special "already gone" handling is needed.
        try:
            await asyncio.to_thread(self._delete_all_versions, path)
        except (BotoCoreError, ClientError) as exc:
            logger.error("B2 delete_object failed | key=%s error=%r", path, exc)
            raise DocumentStorageError(
                "Failed to delete the document from storage."
            ) from exc

    def _delete_all_versions(self, path: str) -> None:
        paginator = self._client.get_paginator("list_object_versions")
        object_ids = [
            {"Key": path, "VersionId": version["VersionId"]}
            for page in paginator.paginate(Bucket=self._bucket, Prefix=path)
            for key in ("Versions", "DeleteMarkers")
            for version in page.get(key, [])
            if version["Key"] == path
        ]
        if not object_ids:
            return
        # delete_objects takes at most 1000 keys per call; a single
        # document's version history will never come close to that.
        self._client.delete_objects(
            Bucket=self._bucket, Delete={"Objects": object_ids}
        )

    async def generate_presigned_url(
        self,
        path: str,
        *,
        filename: str,
        mime_type: str,
        disposition: Disposition,
        expires_in: int = 300,
    ) -> str:
        """Returns a short-lived GET URL for the object. The response
        headers are overridden via the signed query string, so the browser
        gets the original filename and correct MIME type (and opens inline
        vs. downloads) regardless of what was stored with the object.

        Signing is local (no network round-trip), but it can still fail
        on bad credentials/config, so it's wrapped like the other calls.
        """
        try:
            return await asyncio.to_thread(
                self._client.generate_presigned_url,
                "get_object",
                Params={
                    "Bucket": self._bucket,
                    "Key": path,
                    "ResponseContentDisposition": _content_disposition(
                        disposition, filename
                    ),
                    "ResponseContentType": mime_type,
                },
                ExpiresIn=expires_in,
            )
        except (BotoCoreError, ClientError) as exc:
            logger.error(
                "B2 generate_presigned_url failed | key=%s error=%r", path, exc
            )
            raise DocumentStorageError(
                "Failed to generate a download URL for the document."
            ) from exc


@lru_cache
def _b2_provider() -> B2StorageProvider:
    # Building a boto3 client is comparatively expensive (it loads service
    # models from disk), and the config it's built from is itself cached
    # for the process lifetime by get_settings() -- so build it once.
    return B2StorageProvider(get_settings().TRENCH_CONFIG.BACKBLAZE)


def get_storage_provider() -> DocumentStorageProvider:
    return _b2_provider()
