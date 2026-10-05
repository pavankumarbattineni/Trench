"""One-time destructive cleanup: wipes every user-related record from
Postgres, every vector from Pinecone, every object from the Backblaze
bucket, and every Firebase user account.

This is NOT wired into any automated migration or CI step. Run it
manually, only after re-confirming with whoever asked for it that they
want this run in THIS environment right now -- "yes, clean up the dev
environment" said once is not the same as "yes, run this destructive
script against this specific database/bucket/index/project right now."
(Same convention the deleted scripts/wipe_all_users.py used -- see git
history.)

Explicitly preserved, never touched: the `providers` and `provider_models`
tables (the LLM catalog) -- these aren't "user data", they're seeded
platform configuration.

Postgres deletion order (mirrors wipe_all_users.py): Invitations first
(Invitation.invited_by_user_id has no ondelete behavior, so it would block
deleting the inviter), then Users (Document, Credential, Thread ->
ChatHistory, PasswordResetToken all cascade automatically via
ondelete="CASCADE"; Credential.set_by_user_id is SET NULL), then Tenants
(only deletable once no User.tenant_id references them, since that FK has
no ondelete behavior either -- by this point every User row is already
gone, so this always succeeds).

Pinecone: deletes the entire configured index outright (not a per-
namespace wipe) -- simplest way to guarantee nothing is left behind, and
PineconeVectorStore._ensure_index() recreates it lazily, empty, the next
time anything tries to use it.

Backblaze: deletes every object (and every version of it -- the bucket
has versioning enabled, so a delete marker alone would leave prior
versions billed and recoverable) in the configured bucket.

Usage:
    uv run python scripts/cleanup_dev_environment.py --yes-i-am-sure
"""

import asyncio
import sys

from firebase_admin import auth as firebase_auth
from pinecone import Pinecone
from sqlalchemy import delete

from app.config import get_settings
from app.database.models import (
    ChatHistory,
    Credential,
    Document,
    Invitation,
    PasswordResetToken,
    Tenant,
    Thread,
    User,
)
from app.database.session import async_session_factory
from app.service.document_storage_service import _b2_provider
from app.utils.firebase import get_firebase_app


async def wipe_postgres() -> dict[str, int]:
    """Deletes every user-related row, leaving `providers`/`provider_models`
    untouched. ChatHistory/Thread/Document/Credential/PasswordResetToken
    are deleted explicitly rather than relied on purely for cascade, so the
    counts below are accurate regardless of ondelete behavior."""
    async with async_session_factory() as session:
        counts: dict[str, int] = {}

        counts["invitations"] = len(
            (await session.execute(delete(Invitation).returning(Invitation.id)))
            .scalars()
            .all()
        )
        counts["chat_history"] = len(
            (await session.execute(delete(ChatHistory).returning(ChatHistory.id)))
            .scalars()
            .all()
        )
        counts["threads"] = len(
            (await session.execute(delete(Thread).returning(Thread.id)))
            .scalars()
            .all()
        )
        counts["documents"] = len(
            (await session.execute(delete(Document).returning(Document.id)))
            .scalars()
            .all()
        )
        counts["credentials"] = len(
            (await session.execute(delete(Credential).returning(Credential.id)))
            .scalars()
            .all()
        )
        counts["password_reset_tokens"] = len(
            (
                await session.execute(
                    delete(PasswordResetToken).returning(PasswordResetToken.id)
                )
            )
            .scalars()
            .all()
        )
        counts["users"] = len(
            (await session.execute(delete(User).returning(User.id))).scalars().all()
        )
        counts["tenants"] = len(
            (await session.execute(delete(Tenant).returning(Tenant.id)))
            .scalars()
            .all()
        )
        await session.commit()
        return counts


def wipe_pinecone() -> str:
    pinecone_config = get_settings().TRENCH_CONFIG.PINECONE
    client = Pinecone(api_key=pinecone_config.api_key)
    if client.has_index(pinecone_config.index_name):
        client.delete_index(pinecone_config.index_name)
        return pinecone_config.index_name
    return f"{pinecone_config.index_name} (did not exist)"


def wipe_backblaze() -> int:
    client = _b2_provider()._client
    bucket = get_settings().TRENCH_CONFIG.BACKBLAZE.bucket_name
    paginator = client.get_paginator("list_object_versions")
    count = 0
    for page in paginator.paginate(Bucket=bucket):
        object_ids = [
            {"Key": version["Key"], "VersionId": version["VersionId"]}
            for key in ("Versions", "DeleteMarkers")
            for version in page.get(key, [])
        ]
        if not object_ids:
            continue
        # delete_objects takes at most 1000 keys per call.
        for i in range(0, len(object_ids), 1000):
            batch = object_ids[i : i + 1000]
            client.delete_objects(Bucket=bucket, Delete={"Objects": batch})
            count += len(batch)
    return count


def wipe_firebase_users() -> int:
    get_firebase_app()
    count = 0
    page = firebase_auth.list_users()
    while page:
        uids = [user.uid for user in page.users]
        if uids:
            firebase_auth.delete_users(uids)
            count += len(uids)
        page = page.get_next_page()
    return count


async def main() -> None:
    if "--yes-i-am-sure" not in sys.argv:
        print(
            "Refusing to run without --yes-i-am-sure. This permanently "
            "deletes every user-related row from Postgres, wipes the "
            "entire configured Pinecone index, every object in the "
            "configured Backblaze bucket, and every Firebase user. There "
            "is no undo. The providers/provider_models tables are left "
            "untouched."
        )
        sys.exit(1)

    postgres_counts = await wipe_postgres()
    print("Deleted from Postgres:")
    for table, count in postgres_counts.items():
        print(f"  {table}: {count}")

    index_name = wipe_pinecone()
    print(f"Deleted Pinecone index: {index_name}")

    object_count = wipe_backblaze()
    print(f"Deleted {object_count} object version(s) from Backblaze.")

    firebase_count = wipe_firebase_users()
    print(f"Deleted {firebase_count} user(s) from Firebase.")


if __name__ == "__main__":
    asyncio.run(main())
