# Multi-Tenant Invitation-Based RBAC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Trench's open, self-service organization model with strict, invitation-only, three-role (OWNER/ADMIN/MEMBER) multi-tenant RBAC, with SMTP-based invitations and password reset, per-org BYOK credentials, and bulk employee onboarding.

**Architecture:** Backend-first: extend the existing `Organization`/`OrganizationMember`/`KnowledgeAccess` subsystem (already real, not a stub) with an `Invitation` lifecycle, an `OrganizationCredential` table, and a custom `PasswordResetToken` flow, all gated by a tightened role matrix enforced in `OrganizationService` and `app/router/deps.py`. Frontend changes follow the backend contract: remove the self-service org-creation path, add an invite-accept page and bulk-upload UI, and switch password reset off Firebase's client flow onto the new backend endpoints.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 (async), Alembic, PostgreSQL, Firebase Admin SDK, `aiosmtplib` (new dependency, for SMTP), `openpyxl` (new dependency, for `.xlsx` parsing — CSV uses the stdlib `csv` module); Next.js 16 / React 19, `@tanstack/react-query`, `react-hook-form` + `zod`.

**Spec:** `docs/superpowers/specs/2026-10-01-multi-tenant-rbac-design.md`

## Global Constraints

- Do not run any git commands beyond read-only ones (`git status`, `git diff`, `git log`) while executing this plan. Every task's steps that the plan template would normally end with `git add`/`git commit` instead end with **"Mark the task complete"** — no git command. The user reviews and commits everything together at the end, with their own explicit permission.
- Backend: follow the existing `router → service → schemas → database` layering exactly as seen in `app/router/organizations.py`, `app/service/organization_service.py`. Business logic belongs in a `*Service` class with `@staticmethod`/`@classmethod` methods; routers only parse input, call a service, and shape the response.
- All new secrets (invitation tokens, password-reset tokens) are stored **hashed only** (sha256), following the pattern already used for BYOK credentials (never log/store plaintext secrets). Use `hashlib.sha256(raw.encode()).hexdigest()` for hashing — no new crypto dependency needed for this (it's a lookup key, not something decrypted back).
- All new encrypted-at-rest data (`OrganizationCredential.encrypted_credential`) uses the existing `app/utils/encryption.py` (`encrypt_secret`/`decrypt_secret`, `purpose="byok"`) — do not introduce a second encryption scheme.
- Every new backend test follows the pattern in `tests/test_auth_flow.py` / `tests/test_organizations_router.py`: real async DB via `async_session_factory`, Firebase calls mocked via `unittest.mock.patch("app.utils.firebase.verify_firebase_id_token", ...)`, cleanup fixtures that delete rows matching a `test%@example.com`-style pattern. Run backend tests with `uv run pytest tests/<file>.py -v` from `trench_backend/`.
- Every new/changed frontend flow must be exercised in a running browser (`npm run dev` in `trench_frontend/`) before its task is considered done — not just type-checked. Each task's steps say exactly what to click through.
- `trench_frontend/AGENTS.md` warns this is a patched Next.js build with its own docs at `node_modules/next/dist/docs/`. Any task touching routing/middleware-adjacent files (`src/proxy.ts`, new routes under `src/app/`) must mirror the exact structural pattern of an existing, working page/route in this repo (e.g. `src/app/forgot-password/page.tsx`) rather than inventing new Next.js API usage from general knowledge.
- No endpoint in this plan ever accepts a client-supplied `role` for the *acting* user's own permissions — role is always looked up server-side from `OrganizationMember`, never trusted from a request body/header (mirrors the existing `register_as_admin` pattern in `UserService.create_user`).

## Review Focus

- **An ADMIN attempting to invite or assign the `admin` role**: the spec requires ADMINs can only invite/assign `member`. A request (individual or one row of a bulk upload) that tries to set `role=admin` from an ADMIN caller must be rejected (403 individually, or reported as a per-row error in bulk), never silently downgraded or silently allowed.
- **Accepting an invite with a different email than the one invited** (including via Google sign-in where the account's email differs from `invitation.email`): must be rejected with a clear mismatch message, not silently proceed or silently retarget the invitation to the new email.
- **A reused or already-consumed invitation/password-reset token**: both must be single-use where applicable (password-reset always; an invitation moves to `accepted` and a second accept attempt on the same token must fail, not create a duplicate membership or a second user row for the same email).
- **Bulk CSV/Excel upload with a mix of valid and invalid rows**: must not be all-or-nothing — valid rows succeed and send invitations, invalid rows (bad email, disallowed role for the uploader, duplicate/already-invited email) are reported individually, and the uploader can see exactly which rows failed and why.
- **BYOK resolution never leaking a non-Owner member's personal key into an org-KB query**: even if a Member has their own valid `UserCredential`, a company-knowledge-type query must use `OrganizationCredential` (if the Owner set one) or the platform default — never fall through to the querying member's own personal credential.

---

## Task 1: Database schema foundation

**Files:**
- Modify: `trench_backend/app/database/models.py`
- Create (via `alembic revision`): four migration files under `trench_backend/alembic/versions/` (one per logical change, run in sequence — see steps)
- Test: `trench_backend/tests/test_rag_models.py` is the existing home for lightweight model-shape assertions in this codebase; add a new `trench_backend/tests/test_multi_tenant_models.py` for these specifically, to keep the new tables' tests together.

**Interfaces:**
- Produces: `User.organization_id: uuid.UUID | None` (nullable for now; flipped to NOT NULL in Task 11 after the data wipe), `OrganizationMember.role` now accepts `"owner" | "admin" | "member"`, new models `Invitation`, `OrganizationCredential`, `PasswordResetToken` (fields exactly as below) — every later backend task imports these from `app.database.models`.

- [ ] **Step 1: Add the new/changed columns and models to `models.py`**

In `trench_backend/app/database/models.py`, update the `OrganizationMember` docstring/comment at the `role` column (no code change needed to the column itself — it's already `String(16)`, just document the new allowed values):

```python
    # "owner" | "admin" | "member". Exactly one "owner" row exists per
    # organization (the creator, set once at org-creation time via the
    # owner-signup flow -- see AuthService.signup_owner) and it is never
    # changed or removed by anyone, enforced in OrganizationService.
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="member")
```

Add to `User` (after `model_id`):

```python
    # Every user belongs to exactly one organization -- nullable only
    # until the one-time data-wipe migration (see
    # docs/superpowers/plans/2026-10-01-multi-tenant-rbac-plan.md Task 11)
    # flips this to NOT NULL. There is no more "orgless" user going
    # forward; this column, not a join through OrganizationMember, is the
    # primary tenant lookup used by get_current_tenant-style dependencies.
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True, index=True
    )
```

Add three new model classes at the end of the file:

```python
class Invitation(BaseModel):
    """An email invitation to join an organization with a specific role.

    The raw token is emailed once and never stored -- only its sha256
    hash (token_hash) is persisted, the same "secrets are never stored in
    plaintext" rule BYOK credentials already follow. Role is fixed at
    invite time by the inviter (never chosen by the invitee at accept
    time) and is always "admin" or "member" -- an invitation can never
    carry role="owner" (see InvitationService.create).
    """

    __tablename__ = "invitations"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_invitations_token_hash"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # "admin" | "member" -- never "owner"
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="member")
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # "pending" | "accepted" | "revoked" | "expired"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    invited_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class OrganizationCredential(BaseModel):
    """The Owner's BYOK credential, used on behalf of the whole
    organization for company-knowledge queries -- see
    LLMClientService.resolve_for_knowledge. Distinct from UserCredential
    (always per-user, used for personal-knowledge queries and never
    shared). Only ever set/updated by the organization's Owner -- enforced
    in OrganizationCredentialService, not by any DB constraint.
    """

    __tablename__ = "organization_credentials"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "provider_type",
            name="uq_organization_credentials_org_provider",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False)
    encrypted_credential: Mapped[str] = mapped_column(Text, nullable=False)
    set_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    validated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class PasswordResetToken(BaseModel):
    """A single-use, time-limited password reset token, replacing
    reliance on Firebase's own oobCode flow -- see
    PasswordResetService. Same "store only the hash" rule as Invitation.
    """

    __tablename__ = "password_reset_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_password_reset_tokens_token_hash"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
```

- [ ] **Step 2: Generate and hand-fill the migration**

Run from `trench_backend/`:

```bash
uv run alembic revision -m "multi tenant rbac schema foundation"
```

Open the generated file under `alembic/versions/` and fill it in following the exact style of `alembic/versions/38ee4d6e3574_add_role_to_users.py` (plain `op.add_column`/`op.create_table`, a `CheckConstraint` for the role string, no Postgres native enum type):

```python
def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "organization_members",
        sa.Column("role", sa.String(length=16), nullable=False, server_default="member"),
    )
    # The column already exists (added in an earlier migration) -- this
    # migration only adds the CHECK constraint for the new "owner" value.
    op.drop_column("organization_members", "role")
    op.add_column(
        "organization_members",
        sa.Column("role", sa.String(length=16), nullable=False, server_default="member"),
    )
    op.alter_column("organization_members", "role", server_default=None)
    op.create_check_constraint(
        "ck_organization_members_role",
        "organization_members",
        "role IN ('owner', 'admin', 'member')",
    )

    op.add_column(
        "users",
        sa.Column(
            "organization_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
    )
    op.create_index(
        "ix_users_organization_id", "users", ["organization_id"], unique=False
    )
    op.create_foreign_key(
        "fk_users_organization_id", "users", "organizations",
        ["organization_id"], ["id"],
    )

    op.create_table(
        "invitations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="member"),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("invited_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_invitations_organization_id", "invitations", ["organization_id"])
    op.create_index("ix_invitations_email", "invitations", ["email"])
    op.create_unique_constraint("uq_invitations_token_hash", "invitations", ["token_hash"])

    op.create_table(
        "organization_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider_type", sa.String(length=32), nullable=False),
        sa.Column("encrypted_credential", sa.Text(), nullable=False),
        sa.Column("set_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_organization_credentials_organization_id", "organization_credentials", ["organization_id"])
    op.create_unique_constraint(
        "uq_organization_credentials_org_provider", "organization_credentials",
        ["organization_id", "provider_type"],
    )

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint("uq_password_reset_tokens_token_hash", "password_reset_tokens", ["token_hash"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("password_reset_tokens")
    op.drop_table("organization_credentials")
    op.drop_table("invitations")
    op.drop_constraint("fk_users_organization_id", "users", type_="foreignkey")
    op.drop_index("ix_users_organization_id", table_name="users")
    op.drop_column("users", "organization_id")
    op.drop_constraint("ck_organization_members_role", "organization_members", type_="check")
```

Add `from sqlalchemy.dialects import postgresql` to the migration's imports alongside the existing `sqlalchemy as sa` import (check the autogenerated header and add it if missing).

- [ ] **Step 3: Run the migration**

```bash
uv run alembic upgrade head
```
Expected: completes with no errors; `uv run alembic current` shows the new revision.

- [ ] **Step 4: Write and run a model-shape test**

Create `trench_backend/tests/test_multi_tenant_models.py`:

```python
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.database.models import Invitation, OrganizationCredential, PasswordResetToken
from app.database.session import async_session_factory


@pytest.mark.asyncio
async def test_invitation_role_defaults_to_member_and_rejects_owner_at_db_level():
    """There's no DB-level check constraint blocking role='owner' here
    (it's enforced in InvitationService instead -- see Task 4), but this
    test documents the default and that the table round-trips correctly."""
    async with async_session_factory() as session:
        invitation = Invitation(
            organization_id=uuid.uuid4(),
            email="test.invitee@example.com",
            token_hash="a" * 64,
            invited_by_user_id=uuid.uuid4(),
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        # organization_id/invited_by_user_id are fake UUIDs with no
        # matching row -- fine for this smoke test since we roll back
        # instead of committing, never touching FK enforcement.
        session.add(invitation)
        await session.flush()
        assert invitation.role == "member"
        assert invitation.status == "pending"
        await session.rollback()


@pytest.mark.asyncio
async def test_organization_members_role_rejects_invalid_value():
    from sqlalchemy.exc import IntegrityError

    from app.database.models import Organization, OrganizationMember, User

    async with async_session_factory() as session:
        user = User(
            email="test.ownercheck@example.com",
            username="test-ownercheck",
        )
        session.add(user)
        await session.flush()
        org = Organization(
            name=f"test-org-{uuid.uuid4().hex[:8]}",
            domain="test-ownercheck.example.com",
            owner_user_id=user.id,
        )
        session.add(org)
        await session.flush()
        member = OrganizationMember(
            organization_id=org.id, user_id=user.id, role="owner"
        )
        session.add(member)
        await session.flush()
        assert member.role == "owner"

        member.role = "superadmin"
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
```

Run: `uv run pytest tests/test_multi_tenant_models.py -v`
Expected: PASS.

- [ ] **Step 5: Mark the task complete**

---

## Task 2: SMTP email utility

**Files:**
- Create: `trench_backend/app/utils/email.py`
- Modify: `trench_backend/app/config.py`
- Test: `trench_backend/tests/test_email_util.py`

**Interfaces:**
- Produces: `async def send_email(*, to: str, subject: str, html_body: str, text_body: str) -> None` — used by every later task that emails something (invitations, password reset).
- Consumes: a new `TRENCH_CONFIG.SMTP` settings section.

- [ ] **Step 1: Add SMTP config**

In `trench_backend/app/config.py`, add a new settings model and wire it into `TrenchConfig`:

```python
class SMTPConfig(BaseModel):
    host: str
    port: int = 587
    username: str
    password: str
    from_address: str
    use_tls: bool = True
```

Add `SMTP: SMTPConfig` as a field on `TrenchConfig`, alongside `FIREBASE`/`GROQ`/etc.

- [ ] **Step 2: Add `aiosmtplib` as a dependency**

```bash
uv add aiosmtplib
```
Expected: `pyproject.toml`'s `dependencies` list gains `aiosmtplib>=...`.

- [ ] **Step 3: Write the failing test**

```python
# trench_backend/tests/test_email_util.py
from unittest.mock import AsyncMock, patch

import pytest

from app.utils.email import send_email


@pytest.mark.asyncio
async def test_send_email_calls_aiosmtplib_send_with_expected_message():
    with patch("app.utils.email.aiosmtplib.send", new=AsyncMock()) as mock_send:
        await send_email(
            to="test.recipient@example.com",
            subject="Test subject",
            html_body="<p>hello</p>",
            text_body="hello",
        )
    assert mock_send.await_count == 1
    _args, kwargs = mock_send.call_args
    message = kwargs["message"] if "message" in kwargs else mock_send.call_args[0][0]
    assert message["To"] == "test.recipient@example.com"
    assert message["Subject"] == "Test subject"


@pytest.mark.asyncio
async def test_send_email_propagates_smtp_errors():
    with patch(
        "app.utils.email.aiosmtplib.send",
        new=AsyncMock(side_effect=OSError("smtp unreachable")),
    ):
        with pytest.raises(OSError):
            await send_email(
                to="test.recipient@example.com",
                subject="x",
                html_body="x",
                text_body="x",
            )
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_email_util.py -v`
Expected: FAIL (`app.utils.email` doesn't exist yet).

- [ ] **Step 5: Implement**

```python
# trench_backend/app/utils/email.py
"""Sends transactional email over SMTP -- organization invitations and
password-reset links. No third-party email API: TRENCH_CONFIG.SMTP holds
a plain SMTP account's credentials, following the same
TRENCH_CONFIG.<SECTION> pattern as FIREBASE/GROQ/PINECONE.
"""

from email.message import EmailMessage

import aiosmtplib

from app.config import get_settings


async def send_email(*, to: str, subject: str, html_body: str, text_body: str) -> None:
    """Sends one email. Raises whatever aiosmtplib raises on failure --
    callers (InvitationService, PasswordResetService) decide how to
    surface that; this function has no retry/swallow logic of its own.
    """
    smtp = get_settings().TRENCH_CONFIG.SMTP

    message = EmailMessage()
    message["From"] = smtp.from_address
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    await aiosmtplib.send(
        message,
        hostname=smtp.host,
        port=smtp.port,
        username=smtp.username,
        password=smtp.password,
        start_tls=smtp.use_tls,
    )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_email_util.py -v`
Expected: PASS.

- [ ] **Step 7: Add SMTP settings to `.env` / document the required vars**

Add to `trench_backend/.env` (gitignored, local only) the five new `TRENCH_CONFIG__SMTP__*` variables (`HOST`, `PORT`, `USERNAME`, `PASSWORD`, `FROM_ADDRESS`) matching whatever `pydantic-settings` nested-env-var convention the existing `TRENCH_CONFIG__FIREBASE__CREDENTIALS_PATH`-style vars already use in that file — check the existing `.env` for the exact delimiter style used today and match it exactly rather than guessing.

- [ ] **Step 8: Mark the task complete**

---

## Task 3: Organization role-matrix refactor

**Files:**
- Modify: `trench_backend/app/service/organization_service.py`
- Modify: `trench_backend/app/router/deps.py`
- Modify: `trench_backend/app/router/organizations.py` (remove `POST /organizations` and `POST /organizations/{id}/members`, both superseded by invitations)
- Modify: `trench_backend/app/schemas/organization.py`
- Modify: `trench_backend/app/service/knowledge_access_service.py` (owner counts as admin-equivalent)
- Modify: `trench_backend/app/service/user_profile_service.py` (same)
- Test: `trench_backend/tests/test_organizations_router.py` (extend existing file with new role-matrix cases)

**Interfaces:**
- Consumes: nothing new.
- Produces: `OrganizationService.require_owner(db, *, user, organization_id) -> OrganizationMember`, `OrganizationService.require_admin_or_owner(db, *, user, organization_id) -> OrganizationMember` (renamed/generalized from `require_admin`), updated `update_role`/`remove_member` enforcing the matrix in Section 3.1 of the spec. Later tasks (invitations, org credentials) depend on these two dependency functions existing in `deps.py` as `require_org_admin_or_owner` and `require_org_owner`.

- [ ] **Step 1: Write the failing tests**

Add to `trench_backend/tests/test_organizations_router.py` (read the existing file first to match its exact helper functions for creating a signed-up-and-logged-in user and an organization before adding these — reuse those helpers rather than duplicating them):

```python
@pytest.mark.asyncio
async def test_admin_cannot_promote_a_member_to_admin(client: AsyncClient):
    owner_token, org_id = await _signup_owner_and_create_org(client)
    admin_token = await _invite_and_accept(client, org_id, owner_token, role="admin")
    member_token, member_user_id = await _invite_and_accept(
        client, org_id, owner_token, role="member", return_user_id=True
    )

    response = await client.patch(
        f"/api/v1/organizations/{org_id}/members/{member_user_id}",
        json={"role": "admin"},
        headers=_auth_headers(admin_token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_owner_can_promote_a_member_to_admin(client: AsyncClient):
    owner_token, org_id = await _signup_owner_and_create_org(client)
    _member_token, member_user_id = await _invite_and_accept(
        client, org_id, owner_token, role="member", return_user_id=True
    )

    response = await client.patch(
        f"/api/v1/organizations/{org_id}/members/{member_user_id}",
        json={"role": "admin"},
        headers=_auth_headers(owner_token),
    )
    assert response.status_code == 200
    assert response.json()["role"] == "admin"


@pytest.mark.asyncio
async def test_admin_cannot_remove_another_admin(client: AsyncClient):
    owner_token, org_id = await _signup_owner_and_create_org(client)
    admin_one_token = await _invite_and_accept(client, org_id, owner_token, role="admin")
    _admin_two_token, admin_two_user_id = await _invite_and_accept(
        client, org_id, owner_token, role="admin", return_user_id=True
    )

    response = await client.delete(
        f"/api/v1/organizations/{org_id}/members",
        params={"user_id": admin_two_user_id},
        headers=_auth_headers(admin_one_token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_remove_a_member(client: AsyncClient):
    owner_token, org_id = await _signup_owner_and_create_org(client)
    admin_token = await _invite_and_accept(client, org_id, owner_token, role="admin")
    _member_token, member_user_id = await _invite_and_accept(
        client, org_id, owner_token, role="member", return_user_id=True
    )

    response = await client.delete(
        f"/api/v1/organizations/{org_id}/members",
        params={"user_id": member_user_id},
        headers=_auth_headers(admin_token),
    )
    assert response.status_code == 204
```

`_signup_owner_and_create_org` and `_invite_and_accept` don't exist yet — they depend on Tasks 4-8 (invitation + owner-signup flows). **Leave these four tests written but skipped for now**: add `@pytest.mark.skip(reason="depends on Task 8 owner-signup and Task 6 invite-accept")` above each, and come back to un-skip + finish the two helper functions as the final step of Task 6 (once invite-accept exists) and Task 8 (once owner-signup exists) — note this explicitly in those tasks' own steps.

- [ ] **Step 2: Implement the role-matrix changes in `OrganizationService`**

In `trench_backend/app/service/organization_service.py`:

Change `create()`'s member-creation line from `role="admin"` to `role="owner"`, and delete the `_auto_add_domain_users` call and method entirely (onboarding is invitation-only now — no auto-add-by-domain). Update `create()`'s return type/signature to drop the `auto_added` count:

```python
    @staticmethod
    async def create(
        db: AsyncSession, *, creator: User, name: str
    ) -> tuple[Organization, OrganizationMember]:
        """Creates an organization with `creator` as its permanent Owner.

        Invitation-only from here on: no existing user is ever auto-added
        by matching domain (that was the pre-RBAC self-service model --
        see docs/superpowers/specs/2026-10-01-multi-tenant-rbac-design.md).
        Employees join only via InvitationService.accept.
        """
        existing_membership = await OrganizationService.get_membership_for_user(
            db, creator.id
        )
        if existing_membership is not None:
            raise _ALREADY_IN_ORG

        domain = extract_domain(creator.email)
        if is_public_email_domain(domain):
            raise _PUBLIC_DOMAIN
        if await OrganizationService.get_by_domain(db, domain) is not None:
            raise _DOMAIN_TAKEN

        organization = Organization(name=name, domain=domain, owner_user_id=creator.id)
        db.add(organization)
        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            raise _NAME_TAKEN from exc

        member = OrganizationMember(
            organization_id=organization.id, user_id=creator.id, role="owner"
        )
        db.add(member)

        await db.commit()
        await db.refresh(organization)
        await db.refresh(member)
        return organization, member
```

Rename `require_admin` to `require_admin_or_owner` (update its docstring and the role check to `membership.role not in ("admin", "owner")`), and add a new `require_owner`:

```python
    @classmethod
    async def require_admin_or_owner(
        cls, db: AsyncSession, *, user: User, organization_id: uuid.UUID
    ) -> OrganizationMember:
        """Returns the caller's membership row iff they're an Admin or the
        Owner of `organization_id`; raises 403/404 otherwise."""
        organization = await cls.get_by_id(db, organization_id)
        if organization is None:
            raise _NOT_FOUND

        membership = await cls.get_membership_for_user(db, user.id)
        if (
            membership is None
            or membership.organization_id != organization_id
            or membership.role not in ("admin", "owner")
        ):
            raise _NOT_ADMIN
        return membership

    @classmethod
    async def require_owner(
        cls, db: AsyncSession, *, user: User, organization_id: uuid.UUID
    ) -> OrganizationMember:
        """Returns the caller's membership row iff they're the Owner of
        `organization_id`; raises 403/404 otherwise. Used for Owner-only
        actions: promoting a Member to Admin, removing an Admin, setting
        the organization's shared BYOK credential."""
        organization = await cls.get_by_id(db, organization_id)
        if organization is None:
            raise _NOT_FOUND

        membership = await cls.get_membership_for_user(db, user.id)
        if (
            membership is None
            or membership.organization_id != organization_id
            or membership.role != "owner"
        ):
            raise _NOT_OWNER
        return membership
```

Add `_NOT_OWNER = HTTPException(status.HTTP_403_FORBIDDEN, "Only the organization owner can do that")` next to the other module-level exception constants.

Update `update_role` to enforce "only the Owner promotes/demotes, and only between admin/member — the owner role itself is untouchable" (the existing `_require_not_owner` call already blocks changing the *target* owner; add a caller-role check):

```python
    @classmethod
    async def update_role(
        cls,
        db: AsyncSession,
        *,
        organization_id: uuid.UUID,
        target_user_id: uuid.UUID,
        role: str,
        acting_membership: OrganizationMember,
    ) -> OrganizationMember:
        """Raises:
        HTTPException: 404 if the member doesn't exist; 403 if
            `target_user_id` is the organization's permanent owner, if
            `role` is "owner" (never settable this way), or if the caller
            isn't the Owner (only the Owner promotes/demotes).
        """
        if role == "owner" or acting_membership.role != "owner":
            raise _NOT_OWNER
        await cls._require_not_owner(db, organization_id, target_user_id)
        result = await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == target_user_id,
            )
        )
        member = result.scalar_one_or_none()
        if member is None:
            raise _MEMBER_NOT_FOUND
        member.role = role
        await db.commit()
        await db.refresh(member)
        return member
```

Update `remove_member` to enforce "an Admin can only remove a Member, never another Admin":

```python
    @classmethod
    async def remove_member(
        cls,
        db: AsyncSession,
        *,
        organization_id: uuid.UUID,
        target_user_id: uuid.UUID,
        acting_membership: OrganizationMember,
    ) -> None:
        """Raises:
        HTTPException: 409 if the caller is trying to remove themself;
            403 if `target_user_id` is the organization's permanent
            owner, or if the caller is an Admin trying to remove another
            Admin (only the Owner may do that); 404 if the member doesn't
            exist.
        """
        if target_user_id == acting_membership.user_id:
            raise _CANNOT_REMOVE_SELF
        await cls._require_not_owner(db, organization_id, target_user_id)
        result = await db.execute(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.user_id == target_user_id,
            )
        )
        member = result.scalar_one_or_none()
        if member is None:
            raise _MEMBER_NOT_FOUND
        if member.role == "admin" and acting_membership.role != "owner":
            raise _NOT_OWNER
        await db.delete(member)
        await db.commit()
```

Both `update_role` and `remove_member` changed their last positional/keyword argument from `acting_user_id: uuid.UUID` to `acting_membership: OrganizationMember` — update `remove_all_members` similarly isn't required (it already excludes the owner and skips role checks since it never touches admins differently... actually re-read: `remove_all_members` currently removes everyone except owner and caller, with no admin-protection. Leave `remove_all_members` callable by Owner only now — add a role check at its one call site in the router instead, in Step 3 below, rather than inside the service method, since the method itself has no acting-membership role to check against beyond what's already passed).

- [ ] **Step 3: Update `app/router/deps.py`**

Rename the dependency function `require_org_admin` to `require_org_admin_or_owner`, update it to call `OrganizationService.require_admin_or_owner`, and add `require_org_owner`:

```python
async def require_org_admin_or_owner(
    organization_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrganizationMember:
    """Resolves the caller's admin-or-owner membership for
    `organization_id`.

    Raises:
        HTTPException: 404 if the organization doesn't exist, 403 if the
            caller is neither an Admin nor the Owner of it.
    """
    return await OrganizationService.require_admin_or_owner(
        db, user=current_user, organization_id=organization_id
    )


async def require_org_owner(
    organization_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OrganizationMember:
    """Resolves the caller's membership for `organization_id`, requiring
    they're specifically the Owner (not just any Admin).

    Raises:
        HTTPException: 404 if the organization doesn't exist, 403 if the
            caller isn't the Owner of it.
    """
    return await OrganizationService.require_owner(
        db, user=current_user, organization_id=organization_id
    )
```

- [ ] **Step 4: Update `app/router/organizations.py`**

- Delete the `POST ""` (`create_organization`) endpoint entirely (superseded by owner-signup, Task 8) and its now-unused `require_trench_admin` import.
- Delete the `POST "/{organization_id}/members"` (`add_member`, by-username) endpoint entirely (superseded by invitations, Task 5) along with its `_USER_NOT_FOUND` constant and `AddMemberRequest` import if no longer used elsewhere in the file.
- Update every remaining `Depends(require_org_admin)` to `Depends(require_org_admin_or_owner)`, and update the import line accordingly.
- In `update_member_role`, pass `acting_membership=_admin` (rename the parameter binding to `acting_membership` for clarity) instead of a bare user id:

```python
@router.patch(
    "/{organization_id}/members/{user_id}", response_model=OrganizationMemberResponse
)
async def update_member_role(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    body: UpdateMemberRoleRequest,
    acting_membership: OrganizationMember = Depends(require_org_admin_or_owner),
    db: AsyncSession = Depends(get_db),
):
    member = await OrganizationService.update_role(
        db,
        organization_id=organization_id,
        target_user_id=user_id,
        role=body.role,
        acting_membership=acting_membership,
    )
    return await _member_response(db, member)
```

- In `remove_member`, pass `acting_membership=admin` to `OrganizationService.remove_member`, and gate the `remove_all=true` branch behind `Depends(require_org_owner)` instead of `require_org_admin_or_owner` by splitting it into its own code path: since FastAPI can't conditionally pick a dependency per query param, add an explicit check at the top of the handler body:

```python
@router.delete(
    "/{organization_id}/members",
    response_model=OrganizationMemberResponse | MemberBulkRemoveResponse | None,
)
async def remove_member(
    organization_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    remove_all: bool = False,
    acting_membership: OrganizationMember = Depends(require_org_admin_or_owner),
    db: AsyncSession = Depends(get_db),
):
    if remove_all:
        if acting_membership.role != "owner":
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Only the organization owner can remove all members at once",
            )
        if user_id is not None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Provide either user_id or remove_all=true, not both",
            )
        removed_count = await OrganizationService.remove_all_members(
            db, organization_id=organization_id, acting_user_id=acting_membership.user_id
        )
        return MemberBulkRemoveResponse(removed_count=removed_count)

    if user_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "user_id is required unless remove_all=true",
        )
    await OrganizationService.remove_member(
        db,
        organization_id=organization_id,
        target_user_id=user_id,
        acting_membership=acting_membership,
    )
    return None
```

- [ ] **Step 5: Update `OrganizationRole` and `_member_response`'s `is_owner`/`role` typing**

In `trench_backend/app/schemas/organization.py`, change `OrganizationRole = Literal["admin", "member"]` to `Literal["owner", "admin", "member"]`. In `organizations.py`'s `_member_response`, change `has_company_access=has_access or member.role == "admin"` to `has_access or member.role in ("admin", "owner")`.

- [ ] **Step 6: Update owner/admin equivalence in `knowledge_access_service.py` and `user_profile_service.py`**

In `trench_backend/app/service/knowledge_access_service.py`, `authorized_company_organization_id`: change `if membership.role == "admin":` to `if membership.role in ("admin", "owner"):`.

In `trench_backend/app/service/user_profile_service.py`, `build_response`: change `membership.role == "admin"` to `membership.role in ("admin", "owner")`.

- [ ] **Step 7: Run the full existing organizations test suite**

Run: `uv run pytest tests/test_organizations_router.py -v`
Expected: the pre-existing tests that referenced `create_organization`/`add_member`-by-username will now fail (those endpoints are gone) — **this is expected**. Update/remove those specific pre-existing tests to match the new flow (org creation now happens via owner-signup in Task 8, member addition via invitations in Task 5) rather than leaving them broken. Leave the four new role-matrix tests from Step 1 skipped for now.

Expected after cleanup: PASS (minus the four intentionally-skipped tests).

- [ ] **Step 8: Mark the task complete**

---

## Task 4: Invitation service

**Files:**
- Create: `trench_backend/app/schemas/invitation.py`
- Create: `trench_backend/app/service/invitation_service.py`
- Test: `trench_backend/tests/test_invitation_service.py`

**Interfaces:**
- Consumes: `Invitation` model (Task 1), `send_email` (Task 2), `OrganizationService.get_by_id`/`get_membership_for_user` (existing).
- Produces: `InvitationService.create(db, *, organization, inviter_membership, email, role) -> Invitation`, `InvitationService.resend(db, *, invitation_id, inviter_membership) -> Invitation`, `InvitationService.revoke(db, *, invitation_id, inviter_membership) -> None`, `InvitationService.get_by_raw_token(db, raw_token) -> Invitation | None`, `InvitationService.accept(db, *, raw_token, authenticated_email) -> tuple[Invitation, bool]` (bool = whether a new `User` row was created vs. an existing one reused) — Task 5 (router) and Task 6 (accept endpoint) both call these by exactly these names.

- [ ] **Step 1: Write the failing tests**

```python
# trench_backend/tests/test_invitation_service.py
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete

from app.database.models import Invitation, Organization, OrganizationMember, User
from app.database.session import async_session_factory
from app.service.invitation_service import InvitationService, _TooManyRoleError


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        await session.execute(
            delete(Invitation).where(Invitation.email.like("test%@example.com"))
        )
        await session.execute(delete(User).where(User.email.like("test%@example.com")))
        await session.commit()


async def _make_org_and_owner(session) -> tuple[Organization, OrganizationMember]:
    owner = User(email="test.owner@example.com", username="test-owner")
    session.add(owner)
    await session.flush()
    org = Organization(
        name=f"test-org-{uuid.uuid4().hex[:8]}",
        domain="example.com",
        owner_user_id=owner.id,
    )
    session.add(org)
    await session.flush()
    membership = OrganizationMember(
        organization_id=org.id, user_id=owner.id, role="owner"
    )
    session.add(membership)
    await session.flush()
    return org, membership


@pytest.mark.asyncio
async def test_create_sends_an_email_and_stores_only_the_token_hash():
    async with async_session_factory() as session:
        org, owner_membership = await _make_org_and_owner(session)
        await session.commit()

        with patch(
            "app.service.invitation_service.send_email", new=AsyncMock()
        ) as mock_send:
            invitation, raw_token = await InvitationService.create(
                session,
                organization=org,
                inviter_membership=owner_membership,
                email="test.invitee@example.com",
                role="member",
            )

        assert mock_send.await_count == 1
        assert invitation.token_hash == hashlib.sha256(raw_token.encode()).hexdigest()
        assert invitation.status == "pending"
        assert invitation.role == "member"


@pytest.mark.asyncio
async def test_admin_cannot_create_an_admin_role_invitation():
    async with async_session_factory() as session:
        org, owner_membership = await _make_org_and_owner(session)
        await session.commit()

        admin_user = User(email="test.admin@example.com", username="test-admin")
        session.add(admin_user)
        await session.flush()
        admin_membership = OrganizationMember(
            organization_id=org.id, user_id=admin_user.id, role="admin"
        )
        session.add(admin_membership)
        await session.commit()

        with patch("app.service.invitation_service.send_email", new=AsyncMock()):
            with pytest.raises(_TooManyRoleError):
                await InvitationService.create(
                    session,
                    organization=org,
                    inviter_membership=admin_membership,
                    email="test.wannabe-admin@example.com",
                    role="admin",
                )


@pytest.mark.asyncio
async def test_accept_rejects_a_mismatched_email():
    async with async_session_factory() as session:
        org, owner_membership = await _make_org_and_owner(session)
        await session.commit()
        with patch("app.service.invitation_service.send_email", new=AsyncMock()):
            _invitation, raw_token = await InvitationService.create(
                session,
                organization=org,
                inviter_membership=owner_membership,
                email="test.invitee@example.com",
                role="member",
            )

        with pytest.raises(InvitationService.EmailMismatchError):
            await InvitationService.accept(
                session,
                raw_token=raw_token,
                authenticated_email="test.someone-else@example.com",
            )


@pytest.mark.asyncio
async def test_accept_creates_membership_and_grants_default_knowledge_access():
    from app.service.knowledge_access_service import KnowledgeAccessService

    async with async_session_factory() as session:
        org, owner_membership = await _make_org_and_owner(session)
        await session.commit()
        with patch("app.service.invitation_service.send_email", new=AsyncMock()):
            invitation, raw_token = await InvitationService.create(
                session,
                organization=org,
                inviter_membership=owner_membership,
                email="test.invitee@example.com",
                role="member",
            )

        accepted_invitation, user, created = await InvitationService.accept(
            session, raw_token=raw_token, authenticated_email="test.invitee@example.com"
        )

        assert created is True
        assert accepted_invitation.status == "accepted"
        assert user.organization_id == org.id
        has_access = await KnowledgeAccessService.has_company_access(
            session, user_id=user.id, organization_id=org.id
        )
        assert has_access is True


@pytest.mark.asyncio
async def test_accept_rejects_an_already_accepted_token():
    async with async_session_factory() as session:
        org, owner_membership = await _make_org_and_owner(session)
        await session.commit()
        with patch("app.service.invitation_service.send_email", new=AsyncMock()):
            _invitation, raw_token = await InvitationService.create(
                session,
                organization=org,
                inviter_membership=owner_membership,
                email="test.invitee@example.com",
                role="member",
            )
        await InvitationService.accept(
            session, raw_token=raw_token, authenticated_email="test.invitee@example.com"
        )

        with pytest.raises(InvitationService.InvalidTokenError):
            await InvitationService.accept(
                session,
                raw_token=raw_token,
                authenticated_email="test.invitee@example.com",
            )


@pytest.mark.asyncio
async def test_accept_rejects_an_expired_token():
    async with async_session_factory() as session:
        org, owner_membership = await _make_org_and_owner(session)
        await session.commit()
        with patch("app.service.invitation_service.send_email", new=AsyncMock()):
            invitation, raw_token = await InvitationService.create(
                session,
                organization=org,
                inviter_membership=owner_membership,
                email="test.invitee@example.com",
                role="member",
            )
        invitation.expires_at = datetime.now(UTC) - timedelta(days=1)
        await session.commit()

        with pytest.raises(InvitationService.InvalidTokenError):
            await InvitationService.accept(
                session,
                raw_token=raw_token,
                authenticated_email="test.invitee@example.com",
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_invitation_service.py -v`
Expected: FAIL (`app.service.invitation_service` doesn't exist).

- [ ] **Step 3: Write the schemas**

```python
# trench_backend/app/schemas/invitation.py
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr

InvitationRole = Literal["admin", "member"]


class CreateInvitationRequest(BaseModel):
    email: EmailStr
    role: InvitationRole = "member"


class InvitationResponse(BaseModel):
    id: uuid.UUID
    email: str
    role: InvitationRole
    status: Literal["pending", "accepted", "revoked", "expired"]
    expires_at: datetime
    created_at: datetime


class AcceptInvitationRequest(BaseModel):
    token: str
    id_token: str
    username: str | None = None
```

- [ ] **Step 4: Implement `InvitationService`**

```python
# trench_backend/app/service/invitation_service.py
"""Business logic for organization invitations: creating, resending,
revoking, and accepting them.

Only the raw token is ever emailed; only its sha256 hash is persisted
(see Invitation.token_hash) -- the same "secrets never stored in
plaintext" rule BYOK credentials follow (see app/utils/encryption.py),
just hashed rather than encrypted since a token is a lookup key, never
decrypted back.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Invitation, Organization, OrganizationMember, User
from app.service.knowledge_access_service import KnowledgeAccessService
from app.service.user_service import UserService
from app.utils.email import send_email

INVITATION_EXPIRY = timedelta(days=7)


class _TooManyRoleError(Exception):
    """Raised when an Admin (not the Owner) tries to invite someone as
    "admin" -- Admins may only invite Members."""


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def _invite_email_body(*, organization_name: str, role: str, accept_url: str) -> tuple[str, str]:
    text_body = (
        f"You've been invited to join {organization_name} on Trench as a {role}.\n\n"
        f"Accept your invitation: {accept_url}\n\n"
        "This link expires in 7 days."
    )
    html_body = (
        f"<p>You've been invited to join <strong>{organization_name}</strong> "
        f"on Trench as a {role}.</p>"
        f'<p><a href="{accept_url}">Accept your invitation</a></p>'
        "<p>This link expires in 7 days.</p>"
    )
    return html_body, text_body


class InvitationService:
    class InvalidTokenError(Exception):
        """Raised when a token is missing, revoked, expired, or already accepted."""

    class EmailMismatchError(Exception):
        """Raised when the authenticated email doesn't match the invited one."""

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        organization: Organization,
        inviter_membership: OrganizationMember,
        email: str,
        role: str,
    ) -> tuple[Invitation, str]:
        """Creates (or re-uses/rotates) a pending invitation and emails it.

        Raises:
            _TooManyRoleError: if `inviter_membership.role == "admin"` and
                `role != "member"` -- Admins can only invite Members.
        """
        if role not in ("admin", "member"):
            raise ValueError(f"Invalid invitation role: {role!r}")
        if inviter_membership.role == "admin" and role != "member":
            raise _TooManyRoleError("Admins may only invite members")

        raw_token = secrets.token_urlsafe(32)
        token_hash = _hash_token(raw_token)

        result = await db.execute(
            select(Invitation).where(
                Invitation.organization_id == organization.id,
                Invitation.email == email,
                Invitation.status == "pending",
            )
        )
        invitation = result.scalar_one_or_none()
        if invitation is None:
            invitation = Invitation(
                organization_id=organization.id,
                email=email,
                role=role,
                invited_by_user_id=inviter_membership.user_id,
                token_hash=token_hash,
                expires_at=datetime.now(UTC) + INVITATION_EXPIRY,
            )
            db.add(invitation)
        else:
            invitation.role = role
            invitation.token_hash = token_hash
            invitation.expires_at = datetime.now(UTC) + INVITATION_EXPIRY
            invitation.invited_by_user_id = inviter_membership.user_id

        await db.commit()
        await db.refresh(invitation)

        accept_url = f"https://app.trench.example/invite/accept?token={raw_token}"
        html_body, text_body = _invite_email_body(
            organization_name=organization.name, role=role, accept_url=accept_url
        )
        await send_email(
            to=email,
            subject=f"You're invited to join {organization.name} on Trench",
            html_body=html_body,
            text_body=text_body,
        )
        return invitation, raw_token

    @staticmethod
    async def resend(
        db: AsyncSession, *, invitation: Invitation, organization: Organization
    ) -> str:
        """Rotates the token and expiry on an existing pending invitation
        and re-sends the email. Returns the new raw token (for tests;
        production callers don't need it, the email already went out)."""
        raw_token = secrets.token_urlsafe(32)
        invitation.token_hash = _hash_token(raw_token)
        invitation.expires_at = datetime.now(UTC) + INVITATION_EXPIRY
        await db.commit()

        accept_url = f"https://app.trench.example/invite/accept?token={raw_token}"
        html_body, text_body = _invite_email_body(
            organization_name=organization.name, role=invitation.role, accept_url=accept_url
        )
        await send_email(
            to=invitation.email,
            subject=f"Reminder: you're invited to join {organization.name} on Trench",
            html_body=html_body,
            text_body=text_body,
        )
        return raw_token

    @staticmethod
    async def revoke(db: AsyncSession, *, invitation: Invitation) -> None:
        invitation.status = "revoked"
        await db.commit()

    @staticmethod
    async def get_by_id(db: AsyncSession, invitation_id: uuid.UUID) -> Invitation | None:
        return await db.get(Invitation, invitation_id)

    @staticmethod
    async def list_for_organization(
        db: AsyncSession, organization_id: uuid.UUID
    ) -> list[Invitation]:
        result = await db.execute(
            select(Invitation)
            .where(Invitation.organization_id == organization_id)
            .order_by(Invitation.created_at.desc())
        )
        return list(result.scalars().all())

    @classmethod
    async def accept(
        cls, db: AsyncSession, *, raw_token: str, authenticated_email: str
    ) -> tuple[Invitation, User, bool]:
        """Resolves a raw token, validates it, and either creates a new
        User or reuses an existing one (a user invited to a second
        organization isn't possible under the one-org-per-user rule, but
        re-accepting after a prior failed attempt mid-flow should still
        work) -- then creates the OrganizationMember row and, for
        role="member", an immediate default-on KnowledgeAccess grant
        (Owner/Admin need no grant row; their access is role-derived).

        Returns:
            (invitation, user, created) -- `created` is True iff a new
            User row was created by this call.

        Raises:
            InvalidTokenError: token not found, revoked, expired, or
                already accepted.
            EmailMismatchError: `authenticated_email` doesn't exactly
                match `invitation.email`.
        """
        token_hash = _hash_token(raw_token)
        result = await db.execute(
            select(Invitation).where(Invitation.token_hash == token_hash)
        )
        invitation = result.scalar_one_or_none()
        if invitation is None or invitation.status != "pending":
            raise cls.InvalidTokenError("Invitation not found or no longer pending")
        if invitation.expires_at < datetime.now(UTC):
            invitation.status = "expired"
            await db.commit()
            raise cls.InvalidTokenError("Invitation has expired")

        if authenticated_email != invitation.email:
            raise cls.EmailMismatchError(
                f"This invite was sent to {invitation.email}; please sign in "
                "with that address"
            )

        user = await UserService.get_by_email(db, authenticated_email)
        created = False
        if user is None:
            user = await UserService.create_user(db, email=authenticated_email)
            created = True

        user.organization_id = invitation.organization_id
        db.add(
            OrganizationMember(
                organization_id=invitation.organization_id,
                user_id=user.id,
                role=invitation.role,
            )
        )
        invitation.status = "accepted"
        invitation.accepted_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(user)

        if invitation.role == "member":
            await KnowledgeAccessService.grant(
                db, organization_id=invitation.organization_id, user_id=user.id
            )

        return invitation, user, created
```

Note: `UserService.create_user` currently takes `register_as_admin: bool = False` — this call relies on that default and needs no change here, but Task 8 removes `register_as_admin` from the public signup path entirely; confirm in Task 8 that `create_user`'s signature still works for this call site unchanged (it will, since `register_as_admin` has a default).

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_invitation_service.py -v`
Expected: PASS.

- [ ] **Step 6: Mark the task complete**

---

## Task 5: Invitation router endpoints (org-scoped management)

**Files:**
- Modify: `trench_backend/app/router/organizations.py`
- Test: `trench_backend/tests/test_organizations_router.py` (extend)

**Interfaces:**
- Consumes: `InvitationService.create/resend/revoke/list_for_organization/get_by_id` (Task 4), `require_org_admin_or_owner`/`require_org_owner` (Task 3).
- Produces: `POST /organizations/{id}/invitations`, `GET /organizations/{id}/invitations`, `POST /organizations/{id}/invitations/{invitation_id}/resend`, `DELETE /organizations/{id}/invitations/{invitation_id}` — Task 14 (frontend) calls these four by these exact paths/methods.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_admin_can_invite_a_member_but_not_an_admin(client: AsyncClient):
    owner_token, org_id = await _signup_owner_and_create_org(client)
    admin_token = await _invite_and_accept(client, org_id, owner_token, role="admin")

    with patch("app.service.invitation_service.send_email", new=AsyncMock()):
        member_invite = await client.post(
            f"/api/v1/organizations/{org_id}/invitations",
            json={"email": "test.newmember@example.com", "role": "member"},
            headers=_auth_headers(admin_token),
        )
        admin_invite = await client.post(
            f"/api/v1/organizations/{org_id}/invitations",
            json={"email": "test.newadmin@example.com", "role": "admin"},
            headers=_auth_headers(admin_token),
        )
    assert member_invite.status_code == 200
    assert admin_invite.status_code == 403


@pytest.mark.asyncio
async def test_list_invitations_requires_admin_or_owner(client: AsyncClient):
    owner_token, org_id = await _signup_owner_and_create_org(client)
    member_token, _uid = await _invite_and_accept(
        client, org_id, owner_token, role="member", return_user_id=True
    )

    response = await client.get(
        f"/api/v1/organizations/{org_id}/invitations",
        headers=_auth_headers(member_token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_revoke_invitation(client: AsyncClient):
    owner_token, org_id = await _signup_owner_and_create_org(client)
    with patch("app.service.invitation_service.send_email", new=AsyncMock()):
        invite = await client.post(
            f"/api/v1/organizations/{org_id}/invitations",
            json={"email": "test.revokee@example.com", "role": "member"},
            headers=_auth_headers(owner_token),
        )
    invitation_id = invite.json()["id"]

    response = await client.delete(
        f"/api/v1/organizations/{org_id}/invitations/{invitation_id}",
        headers=_auth_headers(owner_token),
    )
    assert response.status_code == 204

    listing = await client.get(
        f"/api/v1/organizations/{org_id}/invitations",
        headers=_auth_headers(owner_token),
    )
    revoked = next(i for i in listing.json() if i["id"] == invitation_id)
    assert revoked["status"] == "revoked"
```

(These three, like Task 3's, depend on `_signup_owner_and_create_org`/`_invite_and_accept` — mark `@pytest.mark.skip` for now, same as Task 3.)

- [ ] **Step 2: Add the endpoints to `organizations.py`**

```python
from app.schemas.invitation import CreateInvitationRequest, InvitationResponse
from app.service.invitation_service import InvitationService, _TooManyRoleError


@router.post("/{organization_id}/invitations", response_model=InvitationResponse)
async def create_invitation(
    organization_id: uuid.UUID,
    body: CreateInvitationRequest,
    acting_membership: OrganizationMember = Depends(require_org_admin_or_owner),
    db: AsyncSession = Depends(get_db),
):
    """Invites an employee by email. Admin or Owner. Admins may only set
    role="member"; only the Owner may invite as "admin".

    Raises:
        HTTPException: 403 if the caller isn't an Admin/Owner, or (Admin
            caller) tries to set role="admin"; 404 if the organization
            doesn't exist.
    """
    organization = await OrganizationService.get_by_id(db, organization_id)
    if organization is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")
    try:
        invitation, _raw_token = await InvitationService.create(
            db,
            organization=organization,
            inviter_membership=acting_membership,
            email=body.email,
            role=body.role,
        )
    except _TooManyRoleError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    return InvitationResponse(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        status=invitation.status,
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
    )


@router.get("/{organization_id}/invitations", response_model=list[InvitationResponse])
async def list_invitations(
    organization_id: uuid.UUID,
    _acting: OrganizationMember = Depends(require_org_admin_or_owner),
    db: AsyncSession = Depends(get_db),
):
    """Lists every invitation (pending, accepted, revoked, expired) for
    the organization. Admin or Owner."""
    invitations = await InvitationService.list_for_organization(db, organization_id)
    return [
        InvitationResponse(
            id=i.id, email=i.email, role=i.role, status=i.status,
            expires_at=i.expires_at, created_at=i.created_at,
        )
        for i in invitations
    ]


async def _require_invitation_in_org(
    db: AsyncSession, *, organization_id: uuid.UUID, invitation_id: uuid.UUID
):
    invitation = await InvitationService.get_by_id(db, invitation_id)
    if invitation is None or invitation.organization_id != organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")
    return invitation


@router.post(
    "/{organization_id}/invitations/{invitation_id}/resend",
    response_model=InvitationResponse,
)
async def resend_invitation(
    organization_id: uuid.UUID,
    invitation_id: uuid.UUID,
    _acting: OrganizationMember = Depends(require_org_admin_or_owner),
    db: AsyncSession = Depends(get_db),
):
    """Rotates the token/expiry and re-sends a pending invitation's
    email. Admin or Owner.

    Raises:
        HTTPException: 404 if the invitation doesn't exist in this org;
            409 if it's no longer pending (already accepted/revoked/expired).
    """
    invitation = await _require_invitation_in_org(
        db, organization_id=organization_id, invitation_id=invitation_id
    )
    if invitation.status != "pending":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Can't resend a {invitation.status} invitation",
        )
    organization = await OrganizationService.get_by_id(db, organization_id)
    await InvitationService.resend(db, invitation=invitation, organization=organization)
    return InvitationResponse(
        id=invitation.id, email=invitation.email, role=invitation.role,
        status=invitation.status, expires_at=invitation.expires_at,
        created_at=invitation.created_at,
    )


@router.delete(
    "/{organization_id}/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_invitation(
    organization_id: uuid.UUID,
    invitation_id: uuid.UUID,
    _acting: OrganizationMember = Depends(require_org_admin_or_owner),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revokes a pending invitation. Admin or Owner.

    Raises:
        HTTPException: 404 if the invitation doesn't exist in this org.
    """
    invitation = await _require_invitation_in_org(
        db, organization_id=organization_id, invitation_id=invitation_id
    )
    await InvitationService.revoke(db, invitation=invitation)
```

- [ ] **Step 3: Run tests (skipped ones stay skipped)**

Run: `uv run pytest tests/test_organizations_router.py -v -k invitation`
Expected: the non-skipped assertions you can exercise without the full owner-signup/accept flow pass; skipped ones show as skipped, not failed.

- [ ] **Step 4: Mark the task complete**

---

## Task 6: Invitation accept endpoint (public) and finishing the Task 3/5 test helpers

**Files:**
- Modify: `trench_backend/app/router/auth.py` (or a new small router — add to `auth.py` since accept is an unauthenticated, identity-establishing action, consistent with signup/login living there)
- Test: `trench_backend/tests/test_invitation_accept_flow.py`
- Modify: `trench_backend/tests/test_organizations_router.py` (un-skip and finish the four Task 3 tests + three Task 5 tests by implementing `_invite_and_accept`)

**Interfaces:**
- Consumes: `InvitationService.accept` (Task 4), `AuthService.verify_firebase_token`/`create_access_token`/`create_refresh_token` (existing).
- Produces: `POST /auth/invitations/accept` returning a `TokenResponse` (same shape as login) — Task 16 (frontend invite-accept page) calls this.

- [ ] **Step 1: Write the failing test**

```python
# trench_backend/tests/test_invitation_accept_flow.py
import time
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import delete

from app.database.models import Invitation, Organization, OrganizationMember, User
from app.database.session import async_session_factory


def _fake_claims(email: str) -> dict:
    return {"uid": f"uid-{email}", "email": email, "iat": int(time.time())}


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        await session.execute(delete(Invitation).where(Invitation.email.like("test%@example.com")))
        await session.execute(delete(User).where(User.email.like("test%@example.com")))
        await session.commit()


async def _create_org_owner_and_pending_invite(
    client: AsyncClient, *, invitee_email: str, role: str = "member"
) -> str:
    """Returns a raw invitation token. Bypasses HTTP for org/invite setup
    (owner-signup and the invite-creation endpoint are covered by their
    own tasks' tests) by writing directly to the DB and capturing the
    raw token InvitationService.create would have emailed."""
    from app.service.invitation_service import InvitationService

    async with async_session_factory() as session:
        owner = User(email="test.accept-owner@example.com", username="test-accept-owner")
        session.add(owner)
        await session.flush()
        org = Organization(
            name="test-accept-org", domain="example.com", owner_user_id=owner.id
        )
        session.add(org)
        await session.flush()
        owner_membership = OrganizationMember(
            organization_id=org.id, user_id=owner.id, role="owner"
        )
        session.add(owner_membership)
        await session.commit()

        with patch("app.service.invitation_service.send_email", new=AsyncMock()):
            _invitation, raw_token = await InvitationService.create(
                session,
                organization=org,
                inviter_membership=owner_membership,
                email=invitee_email,
                role=role,
            )
        return raw_token


@pytest.mark.asyncio
async def test_accept_invitation_returns_a_token_pair(client: AsyncClient):
    raw_token = await _create_org_owner_and_pending_invite(
        client, invitee_email="test.newhire@example.com"
    )

    with patch(
        "app.utils.firebase.verify_firebase_id_token",
        return_value=_fake_claims("test.newhire@example.com"),
    ):
        response = await client.post(
            "/api/v1/auth/invitations/accept",
            json={"token": raw_token, "id_token": "fake", "username": "test-newhire"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]


@pytest.mark.asyncio
async def test_accept_invitation_rejects_mismatched_email(client: AsyncClient):
    raw_token = await _create_org_owner_and_pending_invite(
        client, invitee_email="test.newhire2@example.com"
    )

    with patch(
        "app.utils.firebase.verify_firebase_id_token",
        return_value=_fake_claims("test.wrong-person@example.com"),
    ):
        response = await client.post(
            "/api/v1/auth/invitations/accept",
            json={"token": raw_token, "id_token": "fake"},
        )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_accept_invitation_rejects_an_unknown_token(client: AsyncClient):
    with patch(
        "app.utils.firebase.verify_firebase_id_token",
        return_value=_fake_claims("test.whoever@example.com"),
    ):
        response = await client.post(
            "/api/v1/auth/invitations/accept",
            json={"token": "not-a-real-token", "id_token": "fake"},
        )
    assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_invitation_accept_flow.py -v`
Expected: FAIL (404 route doesn't exist / 404s differently).

- [ ] **Step 3: Add the endpoint**

In `trench_backend/app/router/auth.py`, add:

```python
from app.schemas.invitation import AcceptInvitationRequest
from app.service.invitation_service import InvitationService
from app.service.user_service import UserService
from app.utils.security import create_access_token, create_refresh_token


@router.post("/invitations/accept", response_model=TokenResponse)
async def accept_invitation(
    body: AcceptInvitationRequest,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Accepts an organization invitation: verifies the Firebase ID token,
    validates the invite token, creates (or reuses) the Trench user,
    membership, and default knowledge-access grant, then issues a
    session -- accept and first sign-in are one action here, unlike the
    separate signup/login split everywhere else (there's no meaningful
    "accepted but not yet signed in" state for an invite).

    Args:
        body: The raw invitation token, a verified Firebase ID token, and
            an optional username (used only if this is the invitee's
            first-ever Trench signup).
        db: An active async SQLAlchemy session.

    Returns:
        A fresh access_token/refresh_token pair.

    Raises:
        HTTPException: 401 if the Firebase ID token is invalid; 404 if
            the invitation token doesn't exist or is no longer pending;
            403 if the authenticated email doesn't match the invited one.
    """
    claims = AuthService.verify_firebase_token(body.id_token)
    try:
        _invitation, user, _created = await InvitationService.accept(
            db, raw_token=body.token, authenticated_email=claims["email"]
        )
    except InvitationService.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except InvitationService.EmailMismatchError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc

    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )
```

Add the needed `HTTPException`, `status` imports to `auth.py` if not already present (check the top of the file first).

Note: `InvitationService.accept` creates a `User` via `UserService.create_user(db, email=...)` with no `desired_username` when `body.username` isn't threaded through — update `InvitationService.accept`'s signature to accept an optional `desired_username: str | None = None` parameter and pass it to `UserService.create_user`, and update this router call to pass `desired_username=body.username`. Go back and add that parameter to `accept` in `trench_backend/app/service/invitation_service.py` now (small signature change, not a new task).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_invitation_accept_flow.py -v`
Expected: PASS.

- [ ] **Step 5: Implement the deferred test helpers from Tasks 3 and 5**

In `trench_backend/tests/test_organizations_router.py`, add (near the top, alongside other helpers):

```python
import time
from unittest.mock import AsyncMock, patch


def _fake_claims(email: str) -> dict:
    return {"uid": f"uid-{email}", "email": email, "iat": int(time.time())}


async def _signup_owner_and_create_org(client: AsyncClient) -> tuple[str, str]:
    """Returns (owner_access_token, organization_id)."""
    email = f"test.owner-{uuid.uuid4().hex[:8]}@example.com"
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims(email)
    ):
        response = await client.post(
            "/api/v1/auth/signup/owner",
            json={
                "id_token": "fake",
                "username": f"test-owner-{uuid.uuid4().hex[:6]}",
                "organization_name": f"Test Org {uuid.uuid4().hex[:6]}",
            },
        )
    assert response.status_code == 200, response.text
    return response.json()["access_token"], response.json()["organization"]["id"]


The raw token is never returned by the HTTP API (correctly, for security), so this helper can't retrieve it the way `_create_org_owner_and_pending_invite` in Task 6's own test file does (by calling `InvitationService.create` directly, in-process, bypassing HTTP for setup). Instead, capture the `accept_url` passed to the mocked `send_email` and parse the token out of it, since `send_email` is mocked and its call args are inspectable. The full, correct implementation of `_invite_and_accept` is:

```python
async def _invite_and_accept(
    client: AsyncClient,
    organization_id: str,
    owner_token: str,
    *,
    role: str = "member",
    return_user_id: bool = False,
):
    from urllib.parse import parse_qs, urlparse

    email = f"test.invitee-{uuid.uuid4().hex[:8]}@example.com"
    with patch("app.service.invitation_service.send_email", new=AsyncMock()) as mock_send:
        invite_response = await client.post(
            f"/api/v1/organizations/{organization_id}/invitations",
            json={"email": email, "role": role},
            headers=_auth_headers(owner_token),
        )
    assert invite_response.status_code == 200, invite_response.text

    html_body = mock_send.call_args.kwargs["html_body"]
    accept_url = html_body.split('href="')[1].split('"')[0]
    raw_token = parse_qs(urlparse(accept_url).query)["token"][0]

    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims(email)
    ):
        accept_response = await client.post(
            "/api/v1/auth/invitations/accept",
            json={
                "token": raw_token,
                "id_token": "fake",
                "username": f"test-invitee-{uuid.uuid4().hex[:6]}",
            },
        )
    assert accept_response.status_code == 200, accept_response.text
    access_token = accept_response.json()["access_token"]

    if return_user_id:
        me = await client.get("/api/v1/users/me", headers=_auth_headers(access_token))
        return access_token, me.json()["id"]
    return access_token
```

Remove the `@pytest.mark.skip(...)` decorator from all seven tests written in Tasks 3 and 5 now that both helpers are real.

Note: `_signup_owner_and_create_org` calls `POST /auth/signup/owner`, which doesn't exist until Task 8 — leave those specific tests (the ones using `_signup_owner_and_create_org`) skipped with `@pytest.mark.skip(reason="depends on Task 8 owner-signup endpoint")` until Task 8 lands, and un-skip them there instead. The three Task 5 tests and `test_revoke_invitation`/etc. that only need `_invite_and_accept` (not full owner-signup) can likely still be blocked on it transitively since `_invite_and_accept` needs an existing org+owner token to call — so in practice **all seven stay skipped until Task 8**. Note this explicitly rather than half-un-skipping.

- [ ] **Step 6: Mark the task complete**

---

## Task 7: Bulk CSV/Excel invitation upload

**Files:**
- Modify: `trench_backend/app/router/organizations.py`
- Modify: `trench_backend/app/schemas/invitation.py`
- Test: `trench_backend/tests/test_bulk_invitation_upload.py`

**Interfaces:**
- Consumes: `InvitationService.create` (Task 4).
- Produces: `POST /organizations/{id}/invitations/bulk` (multipart file upload), returning a per-row report — Task 17 (frontend bulk upload UI) consumes this exact response shape.

- [ ] **Step 1: Add `openpyxl` as a dependency**

```bash
uv add openpyxl
```

- [ ] **Step 2: Write the failing test**

```python
# trench_backend/tests/test_bulk_invitation_upload.py
import io
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from tests.test_organizations_router import _auth_headers, _signup_owner_and_create_org


@pytest.mark.asyncio
async def test_bulk_upload_reports_per_row_success_and_failure(client: AsyncClient):
    owner_token, org_id = await _signup_owner_and_create_org(client)
    csv_content = (
        "email,role\n"
        "test.bulk1@example.com,member\n"
        "not-an-email,member\n"
        "test.bulk2@example.com,admin\n"  # fine -- owner uploading
    ).encode()

    with patch("app.service.invitation_service.send_email", new=AsyncMock()):
        response = await client.post(
            f"/api/v1/organizations/{org_id}/invitations/bulk",
            files={"file": ("employees.csv", io.BytesIO(csv_content), "text/csv")},
            headers=_auth_headers(owner_token),
        )

    assert response.status_code == 200
    body = response.json()
    assert len(body["succeeded"]) == 2
    assert len(body["failed"]) == 1
    assert body["failed"][0]["email"] == "not-an-email"


@pytest.mark.asyncio
async def test_bulk_upload_rejects_admin_role_rows_from_an_admin_uploader(client: AsyncClient):
    from tests.test_organizations_router import _invite_and_accept

    owner_token, org_id = await _signup_owner_and_create_org(client)
    admin_token = await _invite_and_accept(client, org_id, owner_token, role="admin")

    csv_content = b"email,role\ntest.bulkadmin@example.com,admin\n"
    with patch("app.service.invitation_service.send_email", new=AsyncMock()):
        response = await client.post(
            f"/api/v1/organizations/{org_id}/invitations/bulk",
            files={"file": ("employees.csv", io.BytesIO(csv_content), "text/csv")},
            headers=_auth_headers(admin_token),
        )

    assert response.status_code == 200
    body = response.json()
    assert len(body["succeeded"]) == 0
    assert len(body["failed"]) == 1
    assert "admin" in body["failed"][0]["reason"].lower()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_bulk_invitation_upload.py -v`
Expected: FAIL (404, endpoint doesn't exist).

- [ ] **Step 4: Add the bulk-upload schemas**

Append to `trench_backend/app/schemas/invitation.py`:

```python
class BulkInvitationRowError(BaseModel):
    row: int
    email: str
    reason: str


class BulkInvitationResult(BaseModel):
    succeeded: list[InvitationResponse]
    failed: list[BulkInvitationRowError]
```

- [ ] **Step 5: Implement the endpoint**

```python
# trench_backend/app/router/organizations.py additions
import csv
import io

import openpyxl
from fastapi import UploadFile
from pydantic import EmailStr, TypeAdapter, ValidationError

from app.schemas.invitation import BulkInvitationResult, BulkInvitationRowError

_email_adapter = TypeAdapter(EmailStr)


def _parse_bulk_rows(filename: str, content: bytes) -> list[tuple[int, str, str]]:
    """Returns a list of (row_number, raw_email, raw_role) tuples, 1-indexed
    by data row (header excluded). Raises ValueError for an unsupported
    file type or missing required columns -- caught by the endpoint and
    turned into a 422, since that's a whole-file problem, not a per-row one.
    """
    if filename.lower().endswith(".csv"):
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None or {"email", "role"} - set(
            f.strip().lower() for f in reader.fieldnames
        ):
            raise ValueError("CSV must have 'email' and 'role' columns")
        return [
            (i + 1, (row.get("email") or "").strip(), (row.get("role") or "member").strip())
            for i, row in enumerate(reader)
        ]
    if filename.lower().endswith(".xlsx"):
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
        sheet = workbook.active
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            raise ValueError("Empty spreadsheet")
        header = [str(c).strip().lower() if c else "" for c in rows[0]]
        if "email" not in header or "role" not in header:
            raise ValueError("Spreadsheet must have 'email' and 'role' columns")
        email_idx, role_idx = header.index("email"), header.index("role")
        return [
            (
                i + 1,
                str(row[email_idx]).strip() if row[email_idx] else "",
                str(row[role_idx]).strip().lower() if role_idx < len(row) and row[role_idx] else "member",
            )
            for i, row in enumerate(rows[1:])
        ]
    raise ValueError("Unsupported file type -- upload a .csv or .xlsx file")


@router.post(
    "/{organization_id}/invitations/bulk", response_model=BulkInvitationResult
)
async def bulk_create_invitations(
    organization_id: uuid.UUID,
    file: UploadFile = File(...),
    acting_membership: OrganizationMember = Depends(require_org_admin_or_owner),
    db: AsyncSession = Depends(get_db),
):
    """Bulk-invites employees from a CSV/XLSX file (columns: email, role).
    Admin or Owner. Every row is validated and processed independently --
    a malformed or disallowed row never blocks the valid rows in the same
    file.

    Raises:
        HTTPException: 404 if the organization doesn't exist; 422 if the
            file itself is unreadable or missing required columns (a
            whole-file problem, distinct from a per-row one, which is
            reported in the response body instead).
    """
    organization = await OrganizationService.get_by_id(db, organization_id)
    if organization is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found")

    content = await file.read()
    try:
        rows = _parse_bulk_rows(file.filename or "", content)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    succeeded: list[InvitationResponse] = []
    failed: list[BulkInvitationRowError] = []
    for row_number, raw_email, raw_role in rows:
        try:
            email = str(_email_adapter.validate_python(raw_email))
        except ValidationError:
            failed.append(
                BulkInvitationRowError(row=row_number, email=raw_email, reason="Invalid email address")
            )
            continue
        role = raw_role if raw_role in ("admin", "member") else "member"
        try:
            invitation, _raw_token = await InvitationService.create(
                db,
                organization=organization,
                inviter_membership=acting_membership,
                email=email,
                role=role,
            )
        except _TooManyRoleError as exc:
            failed.append(BulkInvitationRowError(row=row_number, email=email, reason=str(exc)))
            continue
        succeeded.append(
            InvitationResponse(
                id=invitation.id, email=invitation.email, role=invitation.role,
                status=invitation.status, expires_at=invitation.expires_at,
                created_at=invitation.created_at,
            )
        )

    return BulkInvitationResult(succeeded=succeeded, failed=failed)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_bulk_invitation_upload.py -v`
Expected: PASS.

- [ ] **Step 7: Mark the task complete**

---

## Task 8: Owner-only signup (replaces open signup)

**Files:**
- Modify: `trench_backend/app/router/auth.py`
- Modify: `trench_backend/app/service/auth_service.py`
- Modify: `trench_backend/app/schemas/auth.py`
- Test: `trench_backend/tests/test_auth_flow.py` (the existing open-signup tests change meaning — update them)

**Interfaces:**
- Consumes: `OrganizationService.create` (Task 3's refactored version).
- Produces: `POST /auth/signup/owner` returning `TokenResponse & {"organization": {...}}` (issues a session immediately, unlike the old signup — there's no separate "admin creates the org later" step anymore, so there's nothing gained by deferring login).

- [ ] **Step 1: Write the failing tests**

Add to `trench_backend/tests/test_auth_flow.py`:

```python
@pytest.mark.asyncio
async def test_owner_signup_creates_org_and_logs_in(client: AsyncClient):
    with patch(
        "app.utils.firebase.verify_firebase_id_token",
        return_value=_fake_claims(email="test.newowner@example.com"),
    ):
        response = await client.post(
            "/api/v1/auth/signup/owner",
            json={
                "id_token": "fake",
                "username": "test-newowner",
                "organization_name": "Test Acme Corp",
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["organization"]["name"] == "Test Acme Corp"
    assert body["organization"]["domain"] == "example.com"


@pytest.mark.asyncio
async def test_owner_signup_rejects_a_public_email_domain(client: AsyncClient):
    with patch(
        "app.utils.firebase.verify_firebase_id_token",
        return_value=_fake_claims(email="test.person@gmail.com"),
    ):
        response = await client.post(
            "/api/v1/auth/signup/owner",
            json={"id_token": "fake", "username": "test-gmailowner", "organization_name": "X"},
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_owner_signup_rejects_already_registered_email(client: AsyncClient):
    with patch(
        "app.utils.firebase.verify_firebase_id_token",
        return_value=_fake_claims(email="test.dupeowner@example.com"),
    ):
        first = await client.post(
            "/api/v1/auth/signup/owner",
            json={"id_token": "fake", "username": "test-dupeowner", "organization_name": "Org A"},
        )
        assert first.status_code == 200
        second = await client.post(
            "/api/v1/auth/signup/owner",
            json={"id_token": "fake", "username": "test-dupeowner2", "organization_name": "Org B"},
        )
    assert second.status_code == 409
```

Remove/replace the pre-existing `test_register_as_admin_becomes_admin_only_when_none_exists_yet` and `test_register_as_admin_is_ignored_once_one_exists` and the `register_as_admin=...` usages in `_signup` and `_signup_and_login` — `register_as_admin` is being removed from the public signup surface entirely (Step 2 below). Also remove `test_signup_cannot_self_assign_admin_role_directly`'s `register_as_admin`-adjacent assertion if it relies on that field (re-read the test after Step 2's changes and adjust so it still asserts "no raw role field has any effect," just without referencing `register_as_admin`).

Un-skip the four tests from Task 3 that call `_signup_owner_and_create_org` and the Task 6 test-helper-dependent ones that transitively need it, now that the endpoint exists (do this in `tests/test_organizations_router.py` by removing their `@pytest.mark.skip` decorators).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_auth_flow.py tests/test_organizations_router.py -v`
Expected: the new owner-signup tests FAIL (404); previously-skipped tests now fail too (endpoint missing) rather than skip.

- [ ] **Step 3: Update `app/schemas/auth.py`**

Replace `SignupRequest`/`SignupResponse` usage for the open-signup path. Keep the existing `SignupRequest`/`SignupResponse`/`AdminStatusResponse` classes in place (don't delete — `register_as_admin` as a concept for a separate Trench-platform-admin tool is explicitly out of scope per the spec, not deleted, just no longer reachable from the public signup flow once the router stops exposing it — see Step 4). Add:

```python
class OwnerSignupRequest(BaseModel):
    """POST /auth/signup/owner only -- the sole way an organization (and
    its first user, as Owner) now comes into existence. There is no
    `register_as_admin` field here: Owner supersedes the old Trench
    app-admin bootstrap concept for this flow."""

    id_token: str
    username: Username | None = None
    organization_name: str = Field(min_length=1, max_length=128)


class OwnerSignupOrganization(BaseModel):
    id: uuid.UUID
    name: str
    domain: str


class OwnerSignupResponse(TokenResponse):
    organization: OwnerSignupOrganization
```

- [ ] **Step 4: Update `app/service/auth_service.py`**

Add a new classmethod (leave the existing `signup` method in place, unused by the router after Step 5 but not deleted, per the "don't delete what isn't blocking anything" guidance — or delete it along with its router wiring if you prefer a cleaner diff; either is acceptable, but if you delete it also delete its now-unused test coverage rather than leaving broken references):

```python
    @classmethod
    async def signup_owner(
        cls,
        db: AsyncSession,
        *,
        id_token: str,
        username: str | None,
        organization_name: str,
    ) -> tuple[User, Organization, str, str]:
        """Registers a new Trench user AND the organization they own, as
        one action -- there is no more open signup; every account comes
        into being either this way (Owner, first user of a new company
        domain) or via InvitationService.accept (Member/Admin, invited by
        an existing Owner/Admin).

        Returns:
            (user, organization, access_token, refresh_token) -- unlike
            the old `signup`, this immediately issues a session, since
            there's no separate "admin approves/logs in later" step
            anymore.

        Raises:
            HTTPException: 409 if an account already exists for this
                email, or the organization name/domain is already taken;
                422 if the email is a public/personal provider domain.
        """
        claims = cls.verify_firebase_token(id_token)
        if await UserService.get_by_email(db, claims["email"]) is not None:
            raise _ALREADY_REGISTERED
        user = await UserService.create_user(
            db, email=claims["email"], desired_username=username
        )
        organization, membership = await OrganizationService.create(
            db, creator=user, name=organization_name
        )
        await db.refresh(user)
        return (
            user,
            organization,
            create_access_token(user.id),
            create_refresh_token(user.id),
        )
```

Add the needed imports (`Organization` from `app.database.models`, `OrganizationService` from `app.service.organization_service`) at the top of the file.

- [ ] **Step 5: Update `app/router/auth.py`**

Remove the old `POST /signup` endpoint and the `admin-status` endpoint's only remaining caller context (leave `GET /auth/admin-status` itself in place — harmless, and the spec doesn't ask for its removal, only for the signup page to stop using it, which is a frontend change in Task 15). Add:

```python
@router.post("/signup/owner", response_model=OwnerSignupResponse)
async def signup_owner(
    body: OwnerSignupRequest,
    db: AsyncSession = Depends(get_db),
) -> OwnerSignupResponse:
    """Registers a new Trench user as the Owner of a brand-new
    organization, and immediately issues a session -- the only way an
    organization now comes into existence.

    Args:
        body: The Firebase ID token from signup, an optional username,
            and the new organization's display name (its domain is
            derived from the verified email, never user-entered).
        db: An active async SQLAlchemy session.

    Returns:
        A fresh access_token/refresh_token pair plus the new
        organization's id/name/domain.

    Raises:
        HTTPException: 401 if the Firebase ID token is invalid; 409 if an
            account already exists for this email, the organization name
            is taken, or an organization already exists for this email's
            domain; 422 if the email is a public/personal provider
            domain (Gmail, Yahoo, etc.) rather than a work domain.
    """
    user, organization, access_token, refresh_token = await AuthService.signup_owner(
        db,
        id_token=body.id_token,
        username=body.username,
        organization_name=body.organization_name,
    )
    return OwnerSignupResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        organization=OwnerSignupOrganization(
            id=organization.id, name=organization.name, domain=organization.domain
        ),
    )
```

Decide (and note in the PR description, not in code comments) whether to delete the old `POST /signup` route entirely — recommended, since it's fully superseded and leaving two signup paths live would undermine "invitation-only" — in which case also remove `test_signup_*` tests in `test_auth_flow.py` that exercised the open path and have no equivalent in the new model (keep `test_signup_cannot_self_assign_admin_role_directly`-style intent by asserting the same thing against `/signup/owner` instead, with `role`/`register_as_admin` as the smuggled field).

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_auth_flow.py tests/test_organizations_router.py tests/test_bulk_invitation_upload.py tests/test_invitation_accept_flow.py -v`
Expected: PASS, including every previously-skipped test from Tasks 3, 5, and 7.

- [ ] **Step 7: Mark the task complete**

---

## Task 9: Custom password-reset flow

**Files:**
- Create: `trench_backend/app/service/password_reset_service.py`
- Create: `trench_backend/app/schemas/password_reset.py`
- Modify: `trench_backend/app/router/auth.py`
- Modify: `trench_backend/app/utils/firebase.py`
- Test: `trench_backend/tests/test_password_reset_flow.py`

**Interfaces:**
- Consumes: `send_email` (Task 2), `PasswordResetToken` model (Task 1).
- Produces: `POST /auth/password-reset/request`, `POST /auth/password-reset/confirm`; `app.utils.firebase.set_user_password(firebase_uid_or_email: str, new_password: str) -> None` — Task 20 (frontend) calls the two endpoints by these exact paths.

- [ ] **Step 1: Write the failing tests**

```python
# trench_backend/tests/test_password_reset_flow.py
import time
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import delete

from app.database.models import PasswordResetToken, User
from app.database.session import async_session_factory


def _fake_claims(email: str) -> dict:
    return {"uid": f"uid-{email}", "email": email, "iat": int(time.time())}


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        await session.execute(delete(PasswordResetToken))
        await session.execute(delete(User).where(User.email.like("test%@example.com")))
        await session.commit()


async def _signup(client: AsyncClient, email: str, username: str) -> None:
    with patch(
        "app.utils.firebase.verify_firebase_id_token", return_value=_fake_claims(email)
    ):
        response = await client.post(
            "/api/v1/auth/signup/owner",
            json={"id_token": "fake", "username": username, "organization_name": f"org-{username}"},
        )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_request_reset_always_returns_200_even_for_unknown_email(client: AsyncClient):
    with patch("app.service.password_reset_service.send_email", new=AsyncMock()) as mock_send:
        response = await client.post(
            "/api/v1/auth/password-reset/request",
            json={"email": "test.doesnotexist@example.com"},
        )
    assert response.status_code == 200
    mock_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_request_reset_emails_a_token_for_a_known_user(client: AsyncClient):
    await _signup(client, "test.resetme@example.com", "test-resetme")

    with patch("app.service.password_reset_service.send_email", new=AsyncMock()) as mock_send:
        response = await client.post(
            "/api/v1/auth/password-reset/request",
            json={"email": "test.resetme@example.com"},
        )
    assert response.status_code == 200
    mock_send.assert_awaited_once()


@pytest.mark.asyncio
async def test_confirm_reset_updates_password_via_firebase_admin_sdk(client: AsyncClient):
    await _signup(client, "test.confirmme@example.com", "test-confirmme")

    captured = {}

    async def _capture_send(*, to, subject, html_body, text_body):
        captured["html_body"] = html_body

    with patch(
        "app.service.password_reset_service.send_email", new=_capture_send
    ):
        await client.post(
            "/api/v1/auth/password-reset/request",
            json={"email": "test.confirmme@example.com"},
        )

    from urllib.parse import parse_qs, urlparse

    accept_url = captured["html_body"].split('href="')[1].split('"')[0]
    raw_token = parse_qs(urlparse(accept_url).query)["token"][0]

    with patch("app.utils.firebase.set_user_password", new=AsyncMock()) as mock_set_pw:
        response = await client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": raw_token, "new_password": "a-new-strong-password-1"},
        )
    assert response.status_code == 200
    mock_set_pw.assert_awaited_once()
    assert mock_set_pw.call_args.args[0] == "test.confirmme@example.com"


@pytest.mark.asyncio
async def test_confirm_reset_rejects_a_reused_token(client: AsyncClient):
    await _signup(client, "test.reuseme@example.com", "test-reuseme")
    captured = {}

    async def _capture_send(*, to, subject, html_body, text_body):
        captured["html_body"] = html_body

    with patch("app.service.password_reset_service.send_email", new=_capture_send):
        await client.post(
            "/api/v1/auth/password-reset/request", json={"email": "test.reuseme@example.com"}
        )
    from urllib.parse import parse_qs, urlparse

    accept_url = captured["html_body"].split('href="')[1].split('"')[0]
    raw_token = parse_qs(urlparse(accept_url).query)["token"][0]

    with patch("app.utils.firebase.set_user_password", new=AsyncMock()):
        first = await client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": raw_token, "new_password": "a-new-strong-password-1"},
        )
        second = await client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": raw_token, "new_password": "another-password-2"},
        )
    assert first.status_code == 200
    assert second.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_password_reset_flow.py -v`
Expected: FAIL.

- [ ] **Step 3: Add `set_user_password` to `app/utils/firebase.py`**

```python
async def set_user_password(email: str, new_password: str) -> None:
    """Updates a Firebase user's password via the Admin SDK, looked up by
    email (Trench stores no firebase_uid -- see User's docstring)."""
    get_firebase_app()
    user_record = firebase_auth.get_user_by_email(email)
    firebase_auth.update_user(user_record.uid, password=new_password)
```

Note: `firebase_admin`'s SDK is synchronous; this function is declared `async def` purely so callers can `await` it uniformly with the rest of the auth flow (matches how `verify_firebase_id_token`/`delete_firebase_user` are called — check whether those are awaited anywhere; if the existing codebase calls them synchronously without `await`, make `set_user_password` synchronous too for consistency instead, and adjust the test's `AsyncMock` to a plain `Mock` accordingly — match whichever convention `delete_firebase_user` actually uses today).

- [ ] **Step 4: Write schemas**

```python
# trench_backend/app/schemas/password_reset.py
from pydantic import BaseModel, EmailStr, Field


class RequestPasswordResetRequest(BaseModel):
    email: EmailStr


class ConfirmPasswordResetRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8)
```

- [ ] **Step 5: Implement `PasswordResetService`**

```python
# trench_backend/app/service/password_reset_service.py
"""Fully custom password-reset flow: the backend generates its own
token, emails it via SMTP, and updates the password through the Firebase
Admin SDK -- Firebase remains the password *store*, but owns none of the
reset email or verification flow (replacing sendPasswordResetEmail/
oobCode/verifyPasswordResetCode on the frontend).
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import PasswordResetToken, User
from app.utils import firebase as firebase_utils
from app.utils.email import send_email

RESET_TOKEN_EXPIRY = timedelta(hours=1)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


class PasswordResetService:
    class InvalidTokenError(Exception):
        """Raised when a reset token is missing, expired, or already used."""

    @staticmethod
    async def request(db: AsyncSession, *, email: str) -> None:
        """Always succeeds from the caller's point of view, whether or not
        the email matches an account -- the router returns the same
        response either way to avoid confirming/denying account
        existence (the anti-enumeration UX the old Firebase-native flow
        already had)."""
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user is None:
            return

        raw_token = secrets.token_urlsafe(32)
        token = PasswordResetToken(
            user_id=user.id,
            token_hash=_hash_token(raw_token),
            expires_at=datetime.now(UTC) + RESET_TOKEN_EXPIRY,
        )
        db.add(token)
        await db.commit()

        reset_url = f"https://app.trench.example/reset-password?token={raw_token}"
        await send_email(
            to=email,
            subject="Reset your Trench password",
            html_body=(
                f'<p><a href="{reset_url}">Reset your password</a></p>'
                "<p>This link expires in 1 hour. If you didn't request this, "
                "you can ignore this email.</p>"
            ),
            text_body=(
                f"Reset your password: {reset_url}\n\n"
                "This link expires in 1 hour. If you didn't request this, "
                "you can ignore this email."
            ),
        )

    @classmethod
    async def confirm(cls, db: AsyncSession, *, raw_token: str, new_password: str) -> None:
        """Raises:
        InvalidTokenError: token not found, already used, or expired.
        """
        token_hash = _hash_token(raw_token)
        result = await db.execute(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
        )
        token = result.scalar_one_or_none()
        if token is None or token.used_at is not None:
            raise cls.InvalidTokenError("Reset token not found or already used")
        if token.expires_at < datetime.now(UTC):
            raise cls.InvalidTokenError("Reset token has expired")

        user = await db.get(User, token.user_id)
        await firebase_utils.set_user_password(user.email, new_password)

        token.used_at = datetime.now(UTC)
        await db.commit()
```

- [ ] **Step 6: Add the router endpoints**

In `trench_backend/app/router/auth.py`:

```python
from app.schemas.password_reset import ConfirmPasswordResetRequest, RequestPasswordResetRequest
from app.service.password_reset_service import PasswordResetService


@router.post("/password-reset/request", status_code=200)
async def request_password_reset(
    body: RequestPasswordResetRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """Requests a password-reset email. Always returns 200 regardless of
    whether the email matches an account, to avoid confirming/denying
    account existence."""
    await PasswordResetService.request(db, email=body.email)
    return {"detail": "If that email is registered, a reset link has been sent."}


@router.post("/password-reset/confirm", status_code=200)
async def confirm_password_reset(
    body: ConfirmPasswordResetRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """Completes a password reset.

    Raises:
        HTTPException: 404 if the token is missing, already used, or expired.
    """
    try:
        await PasswordResetService.confirm(
            db, raw_token=body.token, new_password=body.new_password
        )
    except PasswordResetService.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return {"detail": "Password updated."}
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_password_reset_flow.py -v`
Expected: PASS.

- [ ] **Step 8: Mark the task complete**

---

## Task 10: Organization-credential service and endpoints (Owner-only org BYOK)

**Files:**
- Create: `trench_backend/app/service/organization_credential_service.py`
- Create: `trench_backend/app/schemas/organization_credential.py`
- Modify: `trench_backend/app/router/organizations.py`
- Test: `trench_backend/tests/test_organization_credential_service.py`, `trench_backend/tests/test_organization_credentials_router.py`

**Interfaces:**
- Consumes: `OrganizationCredential` model (Task 1), `encrypt_secret`/`decrypt_secret` (existing), `CredentialValidationService.validate` (existing, reused as-is).
- Produces: `OrganizationCredentialService.save/list/delete/get_decrypted` — Task 11 (`LLMClientService`) calls `get_decrypted`.

- [ ] **Step 1: Write the failing service test**

```python
# trench_backend/tests/test_organization_credential_service.py
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete

from app.database.models import Organization, OrganizationCredential, User
from app.database.session import async_session_factory
from app.service.organization_credential_service import OrganizationCredentialService


@pytest.fixture(autouse=True)
async def cleanup():
    yield
    async with async_session_factory() as session:
        await session.execute(delete(OrganizationCredential))
        await session.execute(delete(User).where(User.email.like("test%@example.com")))
        await session.commit()


@pytest.mark.asyncio
async def test_save_then_get_decrypted_round_trips():
    async with async_session_factory() as session:
        owner = User(email="test.credowner@example.com", username="test-credowner")
        session.add(owner)
        await session.flush()
        org = Organization(
            name=f"test-credorg-{uuid.uuid4().hex[:8]}",
            domain="test-cred.example.com",
            owner_user_id=owner.id,
        )
        session.add(org)
        await session.commit()

        with patch(
            "app.service.organization_credential_service.CredentialValidationService.validate",
            new=AsyncMock(),
        ):
            await OrganizationCredentialService.save(
                session,
                organization_id=org.id,
                provider_type="openai_llm",
                api_key="sk-test-123456789",
                set_by_user_id=owner.id,
            )

        decrypted = await OrganizationCredentialService.get_decrypted(
            session, organization_id=org.id, provider_type="openai_llm"
        )
        assert decrypted == "sk-test-123456789"


@pytest.mark.asyncio
async def test_get_decrypted_returns_none_when_unset():
    async with async_session_factory() as session:
        result = await OrganizationCredentialService.get_decrypted(
            session, organization_id=uuid.uuid4(), provider_type="openai_llm"
        )
    assert result is None
```

- [ ] **Step 2: Run to verify failure, then implement**

```python
# trench_backend/app/service/organization_credential_service.py
"""Business logic for an organization's shared BYOK credential -- the
Owner's own key, used on behalf of every member for company-knowledge
queries (see LLMClientService.resolve_for_knowledge). Mirrors
CredentialService (per-user) closely but is scoped to an organization
and settable only by its Owner (enforced by the router dependency, not
here -- this service trusts its caller the same way CredentialService
trusts its router).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import OrganizationCredential
from app.service.credential_validation_service import CredentialValidationService
from app.utils.encryption import decrypt_secret, encrypt_secret


class OrganizationCredentialService:
    @staticmethod
    async def save(
        db: AsyncSession,
        *,
        organization_id: uuid.UUID,
        provider_type: str,
        api_key: str,
        set_by_user_id: uuid.UUID,
    ) -> OrganizationCredential:
        await CredentialValidationService.validate(provider_type, api_key)

        result = await db.execute(
            select(OrganizationCredential).where(
                OrganizationCredential.organization_id == organization_id,
                OrganizationCredential.provider_type == provider_type,
            )
        )
        credential = result.scalar_one_or_none()
        if credential is None:
            credential = OrganizationCredential(
                organization_id=organization_id, provider_type=provider_type
            )
            db.add(credential)

        credential.encrypted_credential = encrypt_secret(api_key, purpose="byok")
        credential.set_by_user_id = set_by_user_id
        credential.validated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(credential)
        return credential

    @staticmethod
    async def list_for_organization(
        db: AsyncSession, *, organization_id: uuid.UUID
    ) -> list[OrganizationCredential]:
        result = await db.execute(
            select(OrganizationCredential).where(
                OrganizationCredential.organization_id == organization_id
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def delete(
        db: AsyncSession, *, organization_id: uuid.UUID, provider_type: str
    ) -> None:
        await db.execute(
            delete(OrganizationCredential).where(
                OrganizationCredential.organization_id == organization_id,
                OrganizationCredential.provider_type == provider_type,
            )
        )
        await db.commit()

    @staticmethod
    async def get_decrypted(
        db: AsyncSession, *, organization_id: uuid.UUID, provider_type: str
    ) -> str | None:
        """Returns the decrypted key, or None if the Owner hasn't set one
        for this provider_type -- callers (LLMClientService) fall back to
        the platform default in that case."""
        result = await db.execute(
            select(OrganizationCredential).where(
                OrganizationCredential.organization_id == organization_id,
                OrganizationCredential.provider_type == provider_type,
            )
        )
        credential = result.scalar_one_or_none()
        if credential is None:
            return None
        return decrypt_secret(credential.encrypted_credential, purpose="byok")
```

Run: `uv run pytest tests/test_organization_credential_service.py -v`
Expected: PASS.

- [ ] **Step 3: Write the failing router test**

```python
# trench_backend/tests/test_organization_credentials_router.py
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from tests.test_organizations_router import _auth_headers, _invite_and_accept, _signup_owner_and_create_org


@pytest.mark.asyncio
async def test_only_owner_can_set_organization_credential(client: AsyncClient):
    owner_token, org_id = await _signup_owner_and_create_org(client)
    admin_token = await _invite_and_accept(client, org_id, owner_token, role="admin")

    with patch(
        "app.service.organization_credential_service.CredentialValidationService.validate",
        new=AsyncMock(),
    ):
        admin_attempt = await client.post(
            f"/api/v1/organizations/{org_id}/credentials",
            json={"provider_type": "openai_llm", "api_key": "sk-test-admin"},
            headers=_auth_headers(admin_token),
        )
        owner_attempt = await client.post(
            f"/api/v1/organizations/{org_id}/credentials",
            json={"provider_type": "openai_llm", "api_key": "sk-test-owner"},
            headers=_auth_headers(owner_token),
        )

    assert admin_attempt.status_code == 403
    assert owner_attempt.status_code == 200
    assert owner_attempt.json()["masked_preview"].startswith("sk-t")
```

- [ ] **Step 4: Add schemas and the router endpoints**

```python
# trench_backend/app/schemas/organization_credential.py
from datetime import datetime

from pydantic import BaseModel


class SaveOrganizationCredentialRequest(BaseModel):
    provider_type: str
    api_key: str


class OrganizationCredentialResponse(BaseModel):
    provider_type: str
    masked_preview: str
    validated_at: datetime
```

In `trench_backend/app/router/organizations.py`:

```python
from app.schemas.organization_credential import (
    OrganizationCredentialResponse,
    SaveOrganizationCredentialRequest,
)
from app.service.organization_credential_service import OrganizationCredentialService


def _mask_key(api_key: str) -> str:
    # Duplicated, 4-line helper rather than importing credential_service's
    # private _mask_key -- matches this codebase's existing convention of
    # not reaching into another module's underscore-prefixed names.
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:4]}{'*' * (len(api_key) - 8)}{api_key[-4:]}"


@router.post(
    "/{organization_id}/credentials", response_model=OrganizationCredentialResponse
)
async def save_organization_credential(
    organization_id: uuid.UUID,
    body: SaveOrganizationCredentialRequest,
    acting_membership: OrganizationMember = Depends(require_org_owner),
    db: AsyncSession = Depends(get_db),
):
    """Sets the organization's shared BYOK credential, used for every
    member's company-knowledge queries. Owner only.

    Raises:
        HTTPException: 403 if the caller isn't the Owner; 422 if the key
            fails provider validation.
    """
    credential = await OrganizationCredentialService.save(
        db,
        organization_id=organization_id,
        provider_type=body.provider_type,
        api_key=body.api_key,
        set_by_user_id=acting_membership.user_id,
    )
    return OrganizationCredentialResponse(
        provider_type=credential.provider_type,
        masked_preview=_mask_key(body.api_key),
        validated_at=credential.validated_at,
    )


@router.get(
    "/{organization_id}/credentials", response_model=list[OrganizationCredentialResponse]
)
async def list_organization_credentials(
    organization_id: uuid.UUID,
    _acting: OrganizationMember = Depends(require_org_owner),
    db: AsyncSession = Depends(get_db),
):
    """Lists the organization's shared BYOK credentials (masked). Owner only."""
    from app.utils.encryption import decrypt_secret

    credentials = await OrganizationCredentialService.list_for_organization(
        db, organization_id=organization_id
    )
    return [
        OrganizationCredentialResponse(
            provider_type=c.provider_type,
            masked_preview=_mask_key(decrypt_secret(c.encrypted_credential, purpose="byok")),
            validated_at=c.validated_at,
        )
        for c in credentials
    ]


@router.delete(
    "/{organization_id}/credentials/{provider_type}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_organization_credential(
    organization_id: uuid.UUID,
    provider_type: str,
    _acting: OrganizationMember = Depends(require_org_owner),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Removes the organization's shared BYOK credential for a provider.
    Owner only -- company-knowledge queries fall back to the platform
    default once this is gone."""
    await OrganizationCredentialService.delete(
        db, organization_id=organization_id, provider_type=provider_type
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_organization_credentials_router.py -v`
Expected: PASS.

- [ ] **Step 6: Mark the task complete**

---

## Task 11: Wire org-credential resolution into `LLMClientService` and the RAG graph

**Files:**
- Modify: `trench_backend/app/service/llm_client_service.py`
- Modify: `trench_backend/app/graph/rag_graph.py`
- Test: `trench_backend/tests/test_llm_client_service_org_resolution.py` (new — check whether a `test_llm_client_service.py` already exists first; if so, extend that instead)

**Interfaces:**
- Consumes: `OrganizationCredentialService.get_decrypted` (Task 10).
- Produces: `LLMClientService.resolve_for_knowledge(db, user, *, knowledge_type, organization_id) -> ResolvedModel` — `rag_graph.py`'s `condense_query` and `generate` nodes call this instead of `resolve_for_user`.

- [ ] **Step 1: Check for an existing LLMClientService test file**

```bash
ls trench_backend/tests/ | grep -i llm_client
```
If one exists, add the new tests there instead of creating `test_llm_client_service_org_resolution.py`; otherwise create it as named above.

- [ ] **Step 2: Write the failing tests**

```python
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.service.llm_client_service import LLMClientService


@pytest.mark.asyncio
async def test_company_query_uses_organization_credential_when_set():
    fake_user = type("U", (), {"id": uuid.uuid4(), "model_id": None})()
    fake_default_model = type(
        "M", (), {"id": uuid.uuid4(), "model_name": "llama-x", "provider_id": uuid.uuid4()}
    )()
    fake_provider = type("P", (), {"name": "openai"})()

    with (
        patch(
            "app.service.llm_client_service.ProviderCatalogService.get_default",
            new=AsyncMock(return_value=fake_default_model),
        ),
        patch(
            "app.service.llm_client_service.OrganizationCredentialService.get_decrypted",
            new=AsyncMock(return_value="org-owned-key"),
        ),
    ):
        async def fake_db_get(model, model_id):
            return fake_provider if model.__name__ == "Provider" else fake_default_model

        fake_db = type("DB", (), {"get": fake_db_get})()

        resolved = await LLMClientService.resolve_for_knowledge(
            fake_db, fake_user, knowledge_type="company", organization_id=uuid.uuid4()
        )
    assert resolved.api_key == "org-owned-key"


@pytest.mark.asyncio
async def test_company_query_never_falls_back_to_a_members_personal_credential():
    """Even if the querying member has their own valid UserCredential,
    a company-knowledge query must not use it -- only OrganizationCredential
    or the platform default."""
    fake_user = type("U", (), {"id": uuid.uuid4(), "model_id": None})()
    fake_default_model = type(
        "M", (), {"id": uuid.uuid4(), "model_name": "llama-x", "provider_id": uuid.uuid4()}
    )()
    fake_provider = type("P", (), {"name": "openai"})()

    with (
        patch(
            "app.service.llm_client_service.ProviderCatalogService.get_default",
            new=AsyncMock(return_value=fake_default_model),
        ),
        patch(
            "app.service.llm_client_service.OrganizationCredentialService.get_decrypted",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.service.llm_client_service.CredentialService.list_credentials",
            new=AsyncMock(
                side_effect=AssertionError(
                    "must not look up the member's personal credentials for a company query"
                )
            ),
        ),
    ):
        async def fake_db_get(model, model_id):
            return fake_provider if model.__name__ == "Provider" else fake_default_model

        fake_db = type("DB", (), {"get": fake_db_get})()

        resolved = await LLMClientService.resolve_for_knowledge(
            fake_db, fake_user, knowledge_type="company", organization_id=uuid.uuid4()
        )
    # Falls back to the platform Groq default rather than ever calling
    # CredentialService.list_credentials for this user.
    assert resolved.provider_name == "groq"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest <the test file> -v`
Expected: FAIL (`resolve_for_knowledge` doesn't exist).

- [ ] **Step 4: Implement `resolve_for_knowledge`**

In `trench_backend/app/service/llm_client_service.py`, add (keep `resolve_for_user` exactly as-is — personal-knowledge queries still use it unchanged):

```python
import uuid

from app.service.organization_credential_service import OrganizationCredentialService


class LLMClientService:
    # ... existing resolve_for_user unchanged ...

    @staticmethod
    async def resolve_for_knowledge(
        db: AsyncSession,
        user: User,
        *,
        knowledge_type: str,
        organization_id: uuid.UUID | None,
    ) -> ResolvedModel:
        """Resolves which model/credential a query should use, branching
        on knowledge_type:

        - "personal": identical to resolve_for_user (the querying user's
          own BYOK credential, or the platform default).
        - "company": the organization's OrganizationCredential if the
          Owner has set one for the resolved provider; otherwise the
          platform default. Never falls back to the querying member's
          own personal UserCredential, even if they have one -- a
          member's personal key only ever powers their personal KB.
        """
        if knowledge_type == "personal" or organization_id is None:
            return await LLMClientService.resolve_for_user(db, user)

        default_model = await ProviderCatalogService.get_default(db)
        model = await db.get(ProviderModel, user.model_id) if user.model_id else None
        model = model or default_model
        provider = await db.get(Provider, model.provider_id)

        if provider.name == "groq":
            return ResolvedModel(
                provider_name="groq",
                model_name=model.model_name,
                api_key=get_settings().TRENCH_CONFIG.GROQ.api_key,
            )

        byok_type = CredentialService.required_credential_type(provider.name)
        org_key = (
            await OrganizationCredentialService.get_decrypted(
                db, organization_id=organization_id, provider_type=byok_type
            )
            if byok_type
            else None
        )
        if org_key is not None:
            return ResolvedModel(
                provider_name=provider.name, model_name=model.model_name, api_key=org_key
            )

        return ResolvedModel(
            provider_name="groq",
            model_name=default_model.model_name,
            api_key=get_settings().TRENCH_CONFIG.GROQ.api_key,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest <the test file> -v`
Expected: PASS.

- [ ] **Step 6: Wire it into `rag_graph.py`**

In `trench_backend/app/graph/rag_graph.py`, change both call sites:

In `condense_query`:
```python
    resolved = await LLMClientService.resolve_for_knowledge(
        db, user,
        knowledge_type=state["knowledge_type"],
        organization_id=uuid.UUID(state["organization_id"]) if state.get("organization_id") else None,
    )
```

In `generate`:
```python
    resolved = await LLMClientService.resolve_for_knowledge(
        db, user,
        knowledge_type=state["knowledge_type"],
        organization_id=uuid.UUID(state["organization_id"]) if state.get("organization_id") else None,
    )
```

- [ ] **Step 7: Run the existing chat-flow test suite to check for regressions**

Run: `uv run pytest tests/test_chat_flow.py -v`
Expected: PASS (personal-knowledge chat behavior is unchanged; this only adds a new branch for company-knowledge that wasn't exercised before).

- [ ] **Step 8: Mark the task complete**

---

## Task 12: Data-wipe script (not auto-run)

**Files:**
- Create: `trench_backend/scripts/wipe_all_users.py`
- Test: none (this is an operational script re-confirmed before every run, not part of the automated suite — see the script's own interactive confirmation in Step 1)

**Interfaces:** none — this is a standalone script, not imported by application code.

- [ ] **Step 1: Write the script**

```python
# trench_backend/scripts/wipe_all_users.py
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

Usage:
    uv run python scripts/wipe_all_users.py --yes-i-am-sure
"""

import asyncio
import sys

import firebase_admin
from firebase_admin import auth as firebase_auth
from sqlalchemy import delete

from app.database.models import User
from app.database.session import async_session_factory
from app.utils.firebase import get_firebase_app


async def wipe_postgres_users() -> int:
    async with async_session_factory() as session:
        result = await session.execute(delete(User).returning(User.id))
        deleted_ids = result.scalars().all()
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
```

Note: `OrganizationMember`/`Document`/`Thread`/`UserCredential` etc. all have `ForeignKey("users.id", ondelete="CASCADE")` per the existing schema (confirmed in `models.py`) — deleting a `User` row cascades to those automatically at the DB level; `Organization.owner_user_id` has **no** `ondelete="CASCADE"` (it's a plain `ForeignKey("users.id")`), so deleting an owner whose organization still exists will raise an `IntegrityError` unless organizations are deleted first. Add that before the user delete:

```python
from app.database.models import Organization

async def wipe_postgres_users() -> int:
    async with async_session_factory() as session:
        await session.execute(delete(Organization))
        result = await session.execute(delete(User).returning(User.id))
        deleted_ids = result.scalars().all()
        await session.commit()
        return len(deleted_ids)
```

- [ ] **Step 2: Do not run this script as part of this plan's execution**

This step is intentionally "do not do it yet" — stop here. Tell the user the script is written and reviewed, and ask them to explicitly confirm, separately from their earlier design-stage approval, that they want it run now against their actual dev database, before anyone runs `uv run python scripts/wipe_all_users.py --yes-i-am-sure`.

- [ ] **Step 3: Mark the task complete** (the script exists and is reviewed; running it is a separate, explicitly-confirmed action, not part of "completing" this task)

---

## Task 13: Flip `users.organization_id` to NOT NULL (post-wipe)

**Files:**
- Create (via `alembic revision`): one migration file
- Test: `trench_backend/tests/test_multi_tenant_models.py` (extend)

**Interfaces:** none new — this only tightens an existing column's nullability.

- [ ] **Step 1: Write the migration**

```bash
uv run alembic revision -m "make users organization_id not null"
```

```python
def upgrade() -> None:
    """Upgrade schema. Run ONLY after scripts/wipe_all_users.py has been
    run and the invitation-only signup/accept flow is the only way a User
    row is created from here on -- both guarantee every remaining/future
    row has organization_id set."""
    op.alter_column("users", "organization_id", nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column("users", "organization_id", nullable=True)
```

- [ ] **Step 2: Do not run this migration yet**

This migration must only run after Task 12's script has actually been executed (by the user, with their explicit go-ahead) against the target database — running it earlier would fail on any pre-existing row with `organization_id IS NULL`. Leave it written and reviewed; note to the user that it's the last step once they're ready to flip the switch.

- [ ] **Step 3: Add a model-level comment update**

In `trench_backend/app/database/models.py`, update the `User.organization_id` column's type hint and comment once the migration above has actually been applied in the target environment (not before, to keep the ORM model in sync with the real schema at every point in between):

```python
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True
    )
```

- [ ] **Step 4: Mark the task complete** (contingent on the user confirming Task 12's script has actually been run first)

---

## Task 14: Frontend — shared types, API client, and the owner-signup flow

**Files:**
- Modify: `trench_frontend/src/lib/api.ts`
- Modify: `trench_frontend/src/lib/auth-service.ts`
- Modify: `trench_frontend/src/app/signup/page.tsx`
- Test: manual browser verification (see Step 5)

**Interfaces:**
- Produces: `UserOrganization.role` includes `"owner"`; `signUpOwner(username, email, password, organizationName) -> OwnerSignupProfile` in `auth-service.ts` — Task 17's organization page and Task 15/16 rely on `UserProfile.organization` being non-null-shaped going forward (still typed nullable during rollout since a brand-new row created before Task 13's migration could theoretically be null, but no *new* code path should ever produce that state after this task).

- [ ] **Step 1: Update `src/lib/api.ts`**

Change:
```ts
export interface UserOrganization {
  id: string;
  name: string;
  role: "owner" | "admin" | "member";
}
```

Add new functions (and a new response type) for owner signup, and remove `register_as_admin`/`getAdminStatus`'s usage from the signup surface (leave `getAdminStatus` itself exported — Task 8's backend kept `GET /auth/admin-status` live even though nothing calls it from signup anymore; delete the frontend function too if nothing else references it, to avoid dead code — grep for other callers first):

```bash
grep -rn "getAdminStatus" trench_frontend/src
```

If `getAdminStatus` has no other callers after this task's signup-page change, delete its export from `api.ts` along with the now-unused `AdminStatusResponse`-shaped inline type.

Add:
```ts
export interface OwnerSignupProfile extends TokenResponse {
  organization: { id: string; name: string; domain: string };
}

export async function signupOwner(
  idToken: string,
  organizationName: string,
  username?: string
): Promise<OwnerSignupProfile> {
  const { data } = await apiClient.post<OwnerSignupProfile>(
    "/api/v1/auth/signup/owner",
    { id_token: idToken, username, organization_name: organizationName }
  );
  setAuthTokensFromOwnerSignup(data);
  return data;
}
```

`setAuthTokensFromOwnerSignup` doesn't exist — the existing `setAuthTokens` function is declared `function setAuthTokens(tokens: TokenResponse)` and is **not exported**; since `OwnerSignupProfile extends TokenResponse`, just call the existing (unexported, module-local) `setAuthTokens(data)` directly instead of inventing a new function name:

```ts
export async function signupOwner(
  idToken: string,
  organizationName: string,
  username?: string
): Promise<OwnerSignupProfile> {
  const { data } = await apiClient.post<OwnerSignupProfile>(
    "/api/v1/auth/signup/owner",
    { id_token: idToken, username, organization_name: organizationName }
  );
  setAuthTokens(data);
  return data;
}
```

Remove `signupWithFirebase`'s `registerAsAdmin` parameter and its `register_as_admin` body field (the old open-signup endpoint is gone per Task 8) — or delete `signupWithFirebase`/`SignupProfile` entirely if Task 8 deleted the backend's old `/auth/signup` route and nothing else in the frontend calls it (grep first, same as above).

- [ ] **Step 2: Update `src/lib/auth-service.ts`**

Replace `signUp`/`signUpWithGoogle` (open signup) with owner-signup equivalents:

```ts
import { signupOwner, type OwnerSignupProfile } from "@/lib/api";

export async function signUpOwner(
  username: string,
  email: string,
  password: string,
  organizationName: string
): Promise<OwnerSignupProfile> {
  const credential = await createUserWithEmailAndPassword(firebaseAuth, email, password);
  const idToken = await credential.user.getIdToken();
  return signupOwner(idToken, organizationName, username);
}

export async function signUpOwnerWithGoogle(
  organizationName: string
): Promise<OwnerSignupProfile> {
  const credential = await signInWithPopup(firebaseAuth, googleProvider);
  const idToken = await credential.user.getIdToken();
  return signupOwner(idToken, organizationName);
}
```

Unlike the old `signUp`/`signUpWithGoogle`, these **do** establish a session immediately (the backend's `/auth/signup/owner` returns tokens directly) — there's no longer a separate sign-in step after signup for the Owner path. Delete the old `signUp`/`signUpWithGoogle` functions.

- [ ] **Step 3: Update `src/app/signup/page.tsx`**

Add an `organizationName` field to the schema, remove `registerAsAdmin`/the admin-status query/the `Switch` toggle entirely, and call `signUpOwner`:

```tsx
const schema = z
  .object({
    username: z
      .string()
      .min(3, "At least 3 characters")
      .max(32, "At most 32 characters")
      .regex(/^[a-zA-Z0-9_-]+$/, "Letters, numbers, - and _ only"),
    organizationName: z.string().min(1, "Required").max(128),
    email: z.string().email("Enter a valid email address"),
    password: z.string().min(8, "At least 8 characters"),
    confirmPassword: z.string(),
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: "Passwords don't match",
    path: ["confirmPassword"],
  });
```

Update the title/subtitle copy to reflect "create your organization" (this is now an org-creation form, not just a personal account form), add the organization-name input field, and change `onSubmit`:

```tsx
const onSubmit = async (values: FormValues) => {
  setFormError(null);
  try {
    await signUpOwner(
      values.username, values.email, values.password, values.organizationName
    );
    // signUpOwner already establishes a session (unlike the old
    // signUp), so route straight into the app instead of to /signin.
    router.push("/chat");
  } catch (error) {
    setFormError(getErrorMessage(error));
  }
};
```

This needs `const router = useRouter();` from `next/navigation` added to the component, and the `AuthProvider`'s `user` state refreshed so the app shell renders correctly on arrival — check how `useAuthSuccess` (used by the sign-in page) does this and call the same refresh mechanism here rather than inventing a new one; if `useAuthSuccess` is reusable as-is (it likely just needs a `UserProfile`-shaped object, and `OwnerSignupProfile` doesn't directly carry one), call `getCurrentUser()` once after `signUpOwner` resolves and feed that into the same `setUser`/navigation logic `useAuthSuccess` uses, rather than duplicating it.

Update `GoogleSignInButton`'s usage on this page: it currently takes `mode="signup"`/`registerAsAdmin`/`onSuccess`/`onError` props. Read `trench_frontend/src/components/google-signin-button.tsx` now and adapt its signup-mode branch to call `signUpOwnerWithGoogle(organizationName)` instead of `signUpWithGoogle(registerAsAdmin)` — this requires the organization-name value to be available before the Google button is clicked (it's a required field), so disable the Google button until `organizationName` is non-empty, with a short inline hint, rather than silently submitting without it.

- [ ] **Step 4: Update `UserResponse`/`UserOrganization` role typing wherever else it's referenced**

```bash
grep -rln '"admin" | "member"' trench_frontend/src
```
Update every match found (expected: `src/lib/organizations.ts`'s `OrganizationRole` type, possibly others) to `"owner" | "admin" | "member"`.

- [ ] **Step 5: Manual verification**

Run `npm run dev` in `trench_frontend/` (with the backend from Tasks 1-11 running locally). In a browser:
1. Go to `/signup`, fill in username/org name/email/password with a fresh work-domain-looking email (not gmail.com), submit.
2. Confirm it lands you signed-in in `/chat` (not `/signin`) with no console errors.
3. Go to `/organization`, confirm it shows the new org with your role as Owner (this page isn't fully updated until Task 17, so a generic/old-looking admin badge is fine for now — just confirm no crash and the organization's name/domain are correct).
4. Try signing up again with a `@gmail.com` email and confirm a clear 422-derived error message is shown, not a raw/ugly error.

- [ ] **Step 6: Mark the task complete**

---

## Task 15: Frontend — invite-accept page

**Files:**
- Create: `trench_frontend/src/app/invite/accept/page.tsx`
- Create: `trench_frontend/src/lib/invitations.ts`
- Modify: `trench_frontend/src/lib/api.ts` (export `TokenResponse`'s setter path reused, nothing else)

**Interfaces:**
- Produces: `acceptInvitation(token, idToken, username?) -> TokenResponse` in `invitations.ts` — this page is the only caller.

- [ ] **Step 1: Add the API client function**

```ts
// trench_frontend/src/lib/invitations.ts
import { apiClient } from "@/lib/api";
import type { TokenResponse } from "@/lib/api";

export async function acceptInvitation(
  token: string,
  idToken: string,
  username?: string
): Promise<TokenResponse> {
  const { data } = await apiClient.post<TokenResponse>(
    "/api/v1/auth/invitations/accept",
    { token, id_token: idToken, username }
  );
  return data;
}
```

- [ ] **Step 2: Build the page**

Mirror the structure of `trench_frontend/src/app/forgot-password/page.tsx` (read it first for the exact `"use client"`/Suspense/layout conventions this repo uses for a standalone auth-adjacent page) and `trench_frontend/src/app/signup/page.tsx` for the form pattern. The page must:

- Read `?token=` from `useSearchParams()` (wrapped in `Suspense`, matching the existing `organization/page.tsx` pattern for the same requirement).
- Show a sign-up-like form (username, password — email is NOT collected, since it's fixed by the invitation and only resolved after the backend call) when the token is present, syntactically valid-looking, and not yet known to be invalid.
- On submit: create (or sign in to) the Firebase account with whatever email the person enters is **not** how this works — the invited email must come from authentication, not a form field the user could mistype. Collect only `username` and `password`, derive the email from a Firebase email/password **signup** attempt using an email field the user does type (Firebase has no way to authenticate without an email) — so the email field IS present on the form, but the backend is the one that rejects a mismatch, not the frontend hiding the field. Keep an email input, but add a visible notice: "You must use the email address this invitation was sent to."
- Call `createUserWithEmailAndPassword` (reuse the existing `firebaseAuth` from `@/lib/firebase`) to get an ID token, then call `acceptInvitation(token, idToken, username)`.
- On a 403 (email mismatch) response, show the exact backend message in the error area (it already says "This invite was sent to X…", which is more useful than a generic message — don't replace it with your own copy).
- On a 404 (invalid/expired/already-accepted token) response, show a distinct "This invitation link is no longer valid — ask your organization admin to resend it" message, not the same copy as the mismatch case.
- On success, store the tokens (the API client's interceptor already reads cookies set elsewhere, but this page gets a raw `TokenResponse`, not a side-effecting call like `loginWithFirebase` — write the cookies directly using the same `Cookies.set` calls `setAuthTokens` in `api.ts` uses by exporting a small `setAuthTokensFromAccept(tokens: TokenResponse)` helper from `api.ts` that just delegates to the existing module-local `setAuthTokens`, since `acceptInvitation` lives in a different file and can't call the unexported one directly):

Add to `trench_frontend/src/lib/api.ts`:
```ts
export function persistTokens(tokens: TokenResponse): void {
  setAuthTokens(tokens);
}
```

Then in the invite-accept page, after a successful `acceptInvitation` call: `persistTokens(tokens)`, then refresh the auth context (same pattern as Task 14's signup flow) and navigate to `/chat`.

- What the person sees for an already-used invite they click twice (e.g. double-submit, or revisiting an old email) must be the clear "no longer valid" message, not a raw network-error look — add a loading state while the request is in flight and disable the submit button to reduce the chance of an accidental double-submit in the first place.

- [ ] **Step 3: Manual verification**

With the backend running: as an Owner (from Task 14's signed-up account), use the backend's `/organizations/{id}/invitations` endpoint directly (e.g. via the FastAPI `/docs` Swagger UI, authenticated as the Owner) to create an invitation for a test email, capture the link from the dev SMTP catch (or print the token/log it temporarily in `InvitationService.create` during this manual test only, then remove the temporary print — don't leave debug prints in committed code), and:
1. Open `/invite/accept?token=...` in the browser.
2. Submit with a *different* email than invited — confirm the mismatch message appears.
3. Submit with the correct invited email — confirm it signs you in and lands on `/chat`.
4. Reload the same `/invite/accept?token=...` URL and try again — confirm the "no longer valid" message appears (token already accepted).

- [ ] **Step 4: Mark the task complete**

---

## Task 16: Frontend — organization page: remove self-service creation, add individual invite + pending-invites list

**Files:**
- Modify: `trench_frontend/src/app/(app)/organization/page.tsx`
- Delete: `trench_frontend/src/app/(app)/organization/create-organization-form.tsx`
- Modify: `trench_frontend/src/app/(app)/organization/members-panel.tsx` (read it first — not shown above; adapt its "add member by username" dialog to "invite by email" instead)
- Create: `trench_frontend/src/app/(app)/organization/invite-member-dialog.tsx` (replaces `add-member-dialog.tsx`'s purpose)
- Create: `trench_frontend/src/app/(app)/organization/pending-invitations-panel.tsx`
- Modify: `trench_frontend/src/lib/organizations.ts`

**Interfaces:**
- Produces: `createInvitation`, `listInvitations`, `resendInvitation`, `revokeInvitation` in `organizations.ts` — Task 17's bulk-upload UI lives alongside these in the same panel.

- [ ] **Step 1: Read the two files this task modifies/replaces before editing**

```bash
cat trench_frontend/src/app/\(app\)/organization/members-panel.tsx
cat trench_frontend/src/app/\(app\)/organization/add-member-dialog.tsx
```
(Not reproduced here since their exact current content wasn't read during planning — read them now and adapt the steps below to their real structure rather than assuming.)

- [ ] **Step 2: Add invitation API client functions to `organizations.ts`**

```ts
export type InvitationStatus = "pending" | "accepted" | "revoked" | "expired";

export interface Invitation {
  id: string;
  email: string;
  role: OrganizationRole;
  status: InvitationStatus;
  expires_at: string;
  created_at: string;
}

export async function createInvitation(
  organizationId: string,
  email: string,
  role: Exclude<OrganizationRole, "owner"> = "member"
): Promise<Invitation> {
  const { data } = await apiClient.post<Invitation>(
    `/api/v1/organizations/${organizationId}/invitations`,
    { email, role }
  );
  return data;
}

export async function listInvitations(organizationId: string): Promise<Invitation[]> {
  const { data } = await apiClient.get<Invitation[]>(
    `/api/v1/organizations/${organizationId}/invitations`
  );
  return data;
}

export async function resendInvitation(
  organizationId: string,
  invitationId: string
): Promise<Invitation> {
  const { data } = await apiClient.post<Invitation>(
    `/api/v1/organizations/${organizationId}/invitations/${invitationId}/resend`
  );
  return data;
}

export async function revokeInvitation(
  organizationId: string,
  invitationId: string
): Promise<void> {
  await apiClient.delete(
    `/api/v1/organizations/${organizationId}/invitations/${invitationId}`
  );
}
```

Remove `addMember` (the old by-username add) from `organizations.ts` — it has no backend endpoint anymore after Task 8.

- [ ] **Step 3: Build `invite-member-dialog.tsx`**

Mirror `add-member-dialog.tsx`'s existing dialog/form structure (shadcn `Dialog`, `react-hook-form` + `zod`, a mutation via `useMutation` invalidating the same query keys the old dialog did) but:
- Replace the "username" input with an "email" input (`z.string().email()`).
- Replace the role select's options: if the current user viewing this dialog is an Owner, offer "Member"/"Admin"; if they're an Admin, offer only "Member" (a disabled/absent "Admin" option, not a selectable-then-rejected one — the backend enforces this too, but don't show a choice the user can't actually make).
- Submit calls `createInvitation` instead of `addMember`.
- Success toast: "Invitation sent to {email}" instead of "{username} added".

- [ ] **Step 4: Build `pending-invitations-panel.tsx`**

A new panel (list, not a dialog) shown within the Members tab, below or alongside the member roster:
- `useQuery` on `listInvitations(organizationId)`.
- Render each invitation: email, role badge, status badge (color-coded: pending=neutral, accepted=success — though accepted ones could be filtered out entirely since they're now just members; show only `status === "pending"` or `"expired"` by default, with a toggle/filter to see the full history if you want one, but the default view should answer "who's still waiting" quickly).
- For `status === "pending"` rows: a "Resend" button (`resendInvitation` mutation) and a "Revoke" button (`revokeInvitation` mutation, with the same shadcn `confirm-dialog.tsx` component the existing member-removal actions in this codebase already use for a destructive confirmation — check `trench_frontend/src/components/confirm-dialog.tsx` for its exact prop shape before using it).
- Both buttons gated the same way the rest of this page gates admin actions: visible only to Owner/Admin, which this panel's own parent (`organization/page.tsx`) already knows via its `isAdmin` prop — thread an `isAdmin: boolean` prop into this panel the same way `MembersPanel` already receives one.

- [ ] **Step 5: Update `organization/page.tsx`**

Delete the entire `notInOrg` branch (dead-end message + `CreateOrganizationForm`) — every authenticated user now has an organization (Task 14 guarantees this at signup; Task 6 guarantees it at invite-accept). Replace the `orgQuery.isLoading` / error branches as needed, but the `notInOrg` 404 case specifically should no longer be reachable in normal operation; keep a minimal fallback (e.g. redirect to `/signin` or show a generic error) in case it's ever hit due to a data inconsistency, rather than assuming it's literally impossible to reach.

Delete `trench_frontend/src/app/(app)/organization/create-organization-form.tsx` entirely (no longer referenced after the above).

Add `<PendingInvitationsPanel organizationId={organizationId} isAdmin={isAdmin} />` into the Members tab's render, alongside the existing `<MembersPanel .../>` and wherever `add-member-dialog`'s trigger button currently lives, replace it with `invite-member-dialog`'s trigger.

- [ ] **Step 6: Manual verification**

In the browser, signed in as the Owner from Task 14:
1. Go to `/organization`, confirm there's no more "create organization" dead-end state anywhere.
2. Click "Invite" / whatever the new trigger is labeled, enter an email + role, submit — confirm a success toast and the new invitation shows up in the pending-invitations list.
3. Click "Resend" on it — confirm it succeeds (check backend logs/dev SMTP catch for a second email).
4. Click "Revoke" — confirm the confirmation dialog appears, confirm it, and the invitation disappears from (or changes status in) the pending list.
5. As an Admin (invite one via the backend `/docs` UI and accept it manually, or reuse Task 15's accept page once it exists), confirm the invite dialog does not offer "Admin" as a role option.

- [ ] **Step 7: Mark the task complete**

---

## Task 17: Frontend — bulk CSV/Excel upload UI

**Files:**
- Modify: `trench_frontend/src/app/(app)/organization/pending-invitations-panel.tsx` (or a new sibling component `bulk-invite-dialog.tsx` triggered from the same area — prefer a separate dialog component, consistent with `invite-member-dialog.tsx` being its own file rather than inlining everything into one panel)
- Create: `trench_frontend/src/app/(app)/organization/bulk-invite-dialog.tsx`
- Modify: `trench_frontend/src/lib/organizations.ts`

**Interfaces:**
- Consumes: `POST /organizations/{id}/invitations/bulk` (Task 7).
- Produces: `bulkCreateInvitations(organizationId, file) -> BulkInvitationResult` in `organizations.ts`.

- [ ] **Step 1: Add the API client function**

```ts
// trench_frontend/src/lib/organizations.ts additions
export interface BulkInvitationRowError {
  row: number;
  email: string;
  reason: string;
}

export interface BulkInvitationResult {
  succeeded: Invitation[];
  failed: BulkInvitationRowError[];
}

export async function bulkCreateInvitations(
  organizationId: string,
  file: File
): Promise<BulkInvitationResult> {
  const formData = new FormData();
  formData.append("file", file);
  const { data } = await apiClient.post<BulkInvitationResult>(
    `/api/v1/organizations/${organizationId}/invitations/bulk`,
    formData,
    { headers: { "Content-Type": "multipart/form-data" } }
  );
  return data;
}
```

- [ ] **Step 2: Build `bulk-invite-dialog.tsx`**

Use the existing `trench_frontend/src/components/documents/upload-dropzone.tsx` as a reference for this codebase's file-upload UI pattern (read it first), but this is a single small file (CSV/XLSX), not the document-upload flow — a simple file `<input type="file" accept=".csv,.xlsx">` plus drag-and-drop is fine; don't pull in the document pipeline's chunked/large-file handling, it doesn't apply here.

Structure:
- A link/button: "Download CSV template" — generates and downloads a static two-column (`email,role`) CSV with one example row, client-side (no backend call needed — construct a `Blob` and trigger a download, a small, self-contained utility function in this same file).
- A file picker.
- On file selection, immediately call `bulkCreateInvitations` (no separate "preview" step — the backend already validates per-row and returns a report, which doubles as the preview).
- While the request is in flight: a loading state (disable the picker, show a spinner — this upload could take a few seconds for a large roster since each row sends an email).
- On response: render two lists — "✓ N invitations sent" (just a count + optionally the emails) and "✗ M rows failed" with each failed row's number, email, and reason shown individually (not collapsed into a single generic error) — this directly matches the Review Focus item on bulk upload reporting.
- Let the dialog stay open after a result so the user can read the failure report before closing, rather than auto-closing on completion.
- Invalidate the pending-invitations list query on success so newly-sent invitations show up immediately without a manual refresh.

- [ ] **Step 3: Wire the trigger into the organization page**

Add a "Bulk upload" button next to the individual "Invite" button from Task 16, both Owner/Admin-gated the same way.

- [ ] **Step 4: Manual verification**

1. Click "Download CSV template", confirm a valid two-column CSV downloads and opens correctly in a spreadsheet app.
2. Edit it to have 2 valid rows and 1 invalid (bad email) row, upload it, confirm the success/failure report matches (2 succeeded, 1 failed with the right reason).
3. As an Admin (not Owner), upload a file with one `role=admin` row — confirm that row is reported as failed with a clear reason, and no admin invitation was actually created (check the pending-invitations list doesn't show it).

- [ ] **Step 5: Mark the task complete**

---

## Task 18: Frontend — organization API key settings (Owner only)

**Files:**
- Modify: `trench_frontend/src/app/(app)/settings/providers/page.tsx`
- Create: `trench_frontend/src/components/settings/organization-api-key-section.tsx`
- Modify: `trench_frontend/src/lib/credentials.ts` or create `trench_frontend/src/lib/organization-credentials.ts` (prefer a separate file, mirroring the backend's separate `OrganizationCredentialService` vs `CredentialService`)

**Interfaces:**
- Consumes: `POST/GET/DELETE /organizations/{id}/credentials` (Task 10).

- [ ] **Step 1: Read `src/components/settings/api-keys-section.tsx` and `src/lib/credentials.ts` first**

These exist already (per the earlier frontend review) — read both now to mirror their exact UI pattern (masked preview display, save/validate/delete flow, error states) rather than inventing a new one.

- [ ] **Step 2: Add the API client**

```ts
// trench_frontend/src/lib/organization-credentials.ts
import { apiClient } from "@/lib/api";

export interface OrganizationCredential {
  provider_type: string;
  masked_preview: string;
  validated_at: string;
}

export async function saveOrganizationCredential(
  organizationId: string,
  providerType: string,
  apiKey: string
): Promise<OrganizationCredential> {
  const { data } = await apiClient.post<OrganizationCredential>(
    `/api/v1/organizations/${organizationId}/credentials`,
    { provider_type: providerType, api_key: apiKey }
  );
  return data;
}

export async function listOrganizationCredentials(
  organizationId: string
): Promise<OrganizationCredential[]> {
  const { data } = await apiClient.get<OrganizationCredential[]>(
    `/api/v1/organizations/${organizationId}/credentials`
  );
  return data;
}

export async function deleteOrganizationCredential(
  organizationId: string,
  providerType: string
): Promise<void> {
  await apiClient.delete(
    `/api/v1/organizations/${organizationId}/credentials/${providerType}`
  );
}
```

- [ ] **Step 3: Build `organization-api-key-section.tsx`**

Structurally mirror `api-keys-section.tsx` exactly (same save/masked-display/delete UI), but:
- Only rendered at all when `user.organization.role === "owner"` (check this exact field path against whatever Task 14 actually named it on `UserProfile` — confirm `organization.role` vs. a differently-named field before writing this check).
- Add one line of explanatory copy this component needs that `api-keys-section.tsx` doesn't: "This key is used for every member's queries against your organization's shared knowledge base. Your personal API keys above are separate and only affect your own personal knowledge base." — placed directly above the key-entry form so the scope distinction is visible at the point of action, not buried in a tooltip.

- [ ] **Step 4: Wire it into `settings/providers/page.tsx`**

Add `<OrganizationApiKeySection />` to the page, conditionally per Step 3's own internal check (so the page doesn't need its own separate role-gating logic duplicated).

- [ ] **Step 5: Manual verification**

1. As the Owner, go to Settings → Providers, confirm the new "Organization API key" section appears with the explanatory copy, save a test key, confirm a masked preview appears, delete it, confirm it's gone.
2. As an Admin or Member, go to the same page, confirm the section is entirely absent (not shown-but-disabled).

- [ ] **Step 6: Mark the task complete**

---

## Task 19: Frontend — custom password reset flow

**Files:**
- Modify: `trench_frontend/src/lib/auth-service.ts`
- Modify: `trench_frontend/src/app/forgot-password/page.tsx`
- Modify: `trench_frontend/src/app/reset-password/page.tsx`
- Modify: `trench_frontend/src/app/reset-password/reset-password-form.tsx`
- Modify: `trench_frontend/src/lib/api.ts`

**Interfaces:**
- Produces: `requestPasswordReset(email)`/`confirmPasswordReset(token, newPassword)` in `auth-service.ts`, now calling the backend instead of Firebase directly.

- [ ] **Step 1: Read the two existing reset-password files first**

```bash
cat "trench_frontend/src/app/reset-password/page.tsx"
cat "trench_frontend/src/app/reset-password/reset-password-form.tsx"
```
(Not reproduced here — adapt the steps below to their actual current structure, particularly how `oobCode` is currently read from the URL and threaded into the form, since `token` replaces it 1:1 but the prop-threading needs to follow the existing component boundary.)

- [ ] **Step 2: Add backend-calling functions to `api.ts`**

```ts
export async function requestPasswordResetEmail(email: string): Promise<void> {
  await apiClient.post("/api/v1/auth/password-reset/request", { email });
}

export async function confirmPasswordResetToken(
  token: string,
  newPassword: string
): Promise<void> {
  await apiClient.post("/api/v1/auth/password-reset/confirm", {
    token,
    new_password: newPassword,
  });
}
```

- [ ] **Step 3: Update `auth-service.ts`**

Replace the three Firebase-native functions:

```ts
export async function requestPasswordReset(email: string): Promise<void> {
  await requestPasswordResetEmail(email);
}

export async function completePasswordReset(
  token: string,
  newPassword: string
): Promise<void> {
  await confirmPasswordResetToken(token, newPassword);
}
```

Delete `verifyResetCode` entirely — there's no separate "verify the code, then show a form" step anymore; the backend's `/password-reset/confirm` validates the token and sets the new password in one call. Remove the now-unused Firebase imports (`sendPasswordResetEmail`, `verifyPasswordResetCode`, `confirmPasswordReset` from `firebase/auth`) from this file.

- [ ] **Step 4: Update `forgot-password/page.tsx`**

No structural change needed beyond confirming it still calls `requestPasswordReset(email)` (same function name, new implementation underneath) — verify the anti-enumeration UX (always showing "check your email" regardless of whether the account exists) still holds, since it's now the backend's job to behave that way (Task 9 already does this), not the frontend's — remove any frontend-side logic that special-cased a "user not found" Firebase error, if any exists, since the backend path never returns one.

- [ ] **Step 5: Update `reset-password/page.tsx` and `reset-password-form.tsx`**

Replace `oobCode` (read from `useSearchParams().get("oobCode")`, per Firebase's convention) with `token` (read from `useSearchParams().get("token")`, per the new backend's convention — Task 15's invite-accept page and Task 9's emailed reset link both use `?token=`, so this matches that convention exactly). Remove the separate `verifyResetCode`-driven "is this code even valid" pre-check step if one exists in the current implementation (re-read what you found in Step 1) — the new flow's single `confirmPasswordResetToken` call is the only validation point, so the form can render immediately without a preliminary verification round-trip; a 404 response from that single call is how an invalid/expired token now surfaces, shown as a form-level error rather than a separate page state.

- [ ] **Step 6: Manual verification**

1. Go to `/forgot-password`, submit a known account's email, confirm the generic "check your email" message.
2. Submit an unknown email, confirm the exact same message (no information leak).
3. Using the dev SMTP catch (or a temporary log statement in `PasswordResetService.request`, removed afterward), get the reset link, open `/reset-password?token=...`, set a new password, confirm success and that you can then sign in with the new password.
4. Reuse the same link again — confirm a clear "link no longer valid" error, not a crash.

- [ ] **Step 7: Mark the task complete**

---

## Final step: whole-plan verification

- [ ] Run the complete backend test suite: `cd trench_backend && uv run pytest -v`. Expected: all tests pass, none skipped except ones explicitly intended to stay skipped (there should be none left after Task 8).
- [ ] Run backend lint/type checks if configured: `uv run ruff check .` from `trench_backend/`.
- [ ] Manually walk the full new-user journey once, end to end, in the browser: Owner signs up → invites an Admin and a Member (one individually, one via bulk CSV) → Admin accepts → Member accepts → Owner sets an org API key → Member asks a question against company knowledge and confirms (via backend logs or a temporary debug field) that the org's credential was used, not the platform default or the member's own key → Owner revokes the Member's knowledge access → Member can no longer query company knowledge → Owner resets their own password via the new flow.
- [ ] Report back to the user with a summary of what was built, any deviations from this plan made during implementation (and why), and explicitly ask whether they want to proceed with Task 12/13's data-wipe-and-lock-down steps now, before anything is committed.
