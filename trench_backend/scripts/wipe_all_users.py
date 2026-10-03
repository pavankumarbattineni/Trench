"""One-time destructive migration: deletes every existing user from both
Postgres and Firebase before the invitation-only multi-tenant system goes
live. Confirmed with the user on 2026-10-01 (see
docs/superpowers/specs/2026-10-01-multi-tenant-rbac-design.md Section 5).

This is NOT wired into any automated migration or CI step. Run it
manually, once, only after the user has re-confirmed they want this run
in THIS environment (dev/staging/prod are different "yes" decisions) --
re-confirm with them immediately before running this, even though the
decision was already made at the design stage; a "yes, delete the data"
decision at design time is not the same as "yes, run this destructive
script against this specific database right now."

Deletion order: Invitations first (Invitation.invited_by_user_id has no
ondelete behavior, so it would block deleting the inviter), then Users
(Document, Thread, Credential, PasswordResetToken, etc. cascade
automatically; Credential.set_by_user_id is SET NULL), then Tenants (only
deletable once no User.tenant_id references them, since that FK has no
ondelete behavior either).

Usage:
    uv run python scripts/wipe_all_users.py --yes-i-am-sure
"""

import asyncio
import sys

from firebase_admin import auth as firebase_auth
from sqlalchemy import delete

from app.database.models import Invitation, Tenant, User
from app.database.session import async_session_factory
from app.utils.firebase import get_firebase_app


async def wipe_postgres_users() -> int:
    async with async_session_factory() as session:
        await session.execute(delete(Invitation))
        result = await session.execute(delete(User).returning(User.id))
        deleted_ids = result.scalars().all()
        await session.execute(delete(Tenant))
        await session.commit()
        return len(deleted_ids)


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
            "deletes every user from Postgres AND Firebase. There is no undo."
        )
        sys.exit(1)

    postgres_count = await wipe_postgres_users()
    print(f"Deleted {postgres_count} user(s) from Postgres.")
    firebase_count = wipe_firebase_users()
    print(f"Deleted {firebase_count} user(s) from Firebase.")


if __name__ == "__main__":
    asyncio.run(main())
