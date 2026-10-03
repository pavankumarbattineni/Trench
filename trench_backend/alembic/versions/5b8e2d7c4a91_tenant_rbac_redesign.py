"""tenant rbac redesign: organizations -> tenants, role/grant/usage on users,
unified credentials table

Revision ID: 5b8e2d7c4a91
Revises: 3873b7cda9af, 70ed057f9456
Create Date: 2026-10-03 11:00:00.000000

Implements docs/superpowers/specs/2026-10-03-tenant-rbac-redesign-design.md
plus two further table merges, in one migration:

1. Tenant/RBAC: `organizations` -> `tenants` (drops `owner_user_id`),
   `organization_members` dropped, `users.organization_id` ->
   `users.tenant_id`, and `users.role` repurposed from the old Trench app
   role ('admin'/'user') to the tenant role ('owner'/'admin'/'member').
   `organization_id` -> `tenant_id` on documents and invitations too.
2. `knowledge_access` + `usage_counters` folded onto `users` as
   `has_company_knowledge_access` / `documents_uploaded_count`.
3. `user_credentials` + `organization_credentials` merged into one
   `credentials` table, owned by exactly one of user_id/tenant_id (CHECK),
   with per-scope partial unique indexes. `set_by_user_id` now has
   ON DELETE SET NULL (it previously had no ondelete at all, which would
   block deleting any user who had ever set a tenant credential).

Also merges the two heads that previously branched off c1a3fa88c33a
(3873b7cda9af and 70ed057f9456) back into one linear history.

Destructive by design: no environment has data that must survive this
(same stance as 70ed057f9456 / scripts/wipe_all_users.py). Where carrying
existing rows over is a one-statement UPDATE/INSERT ... SELECT, upgrade()
does so anyway -- roles (needed regardless, since the old 'user' value
violates the new ck_users_role), grants, usage counts, and credentials --
so a populated dev database keeps working without a wipe. Running
scripts/wipe_all_users.py first is still a valid, simpler alternative.

`users.tenant_id` becomes NULLable here (70ed057f9456 had made
organization_id NOT NULL): removing a member from their tenant clears it
rather than deleting their account, which a NOT NULL column can't express.
Both onboarding paths still always set it.

downgrade() restores the previous schema shape on a best-effort basis; it
needs every user to still belong to a tenant (the old NOT NULL), so wipe
first if any tenantless users exist.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "5b8e2d7c4a91"
down_revision: Union[str, Sequence[str], None] = ("3873b7cda9af", "70ed057f9456")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _base_columns() -> list[sa.Column]:
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    """Upgrade schema."""
    # --- users.role: app role ('admin'/'user') -> tenant role -------------
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.execute(
        """
        UPDATE users
        SET role = COALESCE(
            (SELECT om.role FROM organization_members om WHERE om.user_id = users.id),
            'member'
        )
        """
    )
    op.create_check_constraint(
        "ck_users_role", "users", "role IN ('owner', 'admin', 'member')"
    )

    # --- knowledge_access + usage_counters -> columns on users ------------
    op.add_column(
        "users",
        sa.Column(
            "has_company_knowledge_access",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "documents_uploaded_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.execute(
        """
        UPDATE users SET has_company_knowledge_access = true
        WHERE EXISTS (
            SELECT 1 FROM knowledge_access ka
            WHERE ka.user_id = users.id
              AND ka.organization_id = users.organization_id
              AND ka.knowledge_type = 'company'
              AND ka.is_active
        )
        """
    )
    op.execute(
        """
        UPDATE users SET documents_uploaded_count = uc.documents_uploaded_count
        FROM usage_counters uc WHERE uc.user_id = users.id
        """
    )
    op.drop_table("knowledge_access")
    op.drop_table("usage_counters")
    op.drop_table("organization_members")

    # --- organizations -> tenants ------------------------------------------
    op.drop_constraint("fk_organizations_owner_user_id", "organizations", type_="foreignkey")
    op.drop_column("organizations", "owner_user_id")
    op.rename_table("organizations", "tenants")
    op.execute("ALTER TABLE tenants RENAME CONSTRAINT organizations_pkey TO tenants_pkey")
    op.execute("ALTER TABLE tenants RENAME CONSTRAINT organizations_name_key TO tenants_name_key")
    op.execute("ALTER TABLE tenants RENAME CONSTRAINT uq_organizations_domain TO uq_tenants_domain")

    # --- organization_id -> tenant_id (users, documents, invitations) -----
    op.alter_column("users", "organization_id", new_column_name="tenant_id", nullable=True)
    op.execute("ALTER INDEX ix_users_organization_id RENAME TO ix_users_tenant_id")
    op.execute("ALTER TABLE users RENAME CONSTRAINT fk_users_organization_id TO fk_users_tenant_id")

    op.alter_column("documents", "organization_id", new_column_name="tenant_id")
    op.execute("ALTER INDEX ix_documents_organization_id RENAME TO ix_documents_tenant_id")
    op.execute(
        "ALTER TABLE documents RENAME CONSTRAINT documents_organization_id_fkey "
        "TO documents_tenant_id_fkey"
    )
    op.execute(
        "ALTER TABLE documents RENAME CONSTRAINT uq_documents_organization_content_hash "
        "TO uq_documents_tenant_content_hash"
    )

    op.alter_column("invitations", "organization_id", new_column_name="tenant_id")
    op.execute("ALTER INDEX ix_invitations_organization_id RENAME TO ix_invitations_tenant_id")
    op.execute(
        "ALTER TABLE invitations RENAME CONSTRAINT invitations_organization_id_fkey "
        "TO invitations_tenant_id_fkey"
    )

    # --- user_credentials + organization_credentials -> credentials -------
    op.create_table(
        "credentials",
        *_base_columns(),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True),
        sa.Column("provider_type", sa.String(length=32), nullable=False),
        sa.Column("encrypted_credential", sa.Text(), nullable=False),
        sa.Column("set_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(user_id IS NULL) <> (tenant_id IS NULL)",
            name="ck_credentials_single_owner",
        ),
    )
    op.create_index("ix_credentials_user_id", "credentials", ["user_id"])
    op.create_index("ix_credentials_tenant_id", "credentials", ["tenant_id"])
    op.create_index(
        "uq_credentials_user_provider",
        "credentials",
        ["user_id", "provider_type"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.create_index(
        "uq_credentials_tenant_provider",
        "credentials",
        ["tenant_id", "provider_type"],
        unique=True,
        postgresql_where=sa.text("tenant_id IS NOT NULL"),
    )
    op.execute(
        """
        INSERT INTO credentials (id, is_active, created_at, updated_at, user_id,
                                 provider_type, encrypted_credential, validated_at)
        SELECT id, is_active, created_at, updated_at, user_id,
               provider_type, encrypted_credential, validated_at
        FROM user_credentials
        """
    )
    op.execute(
        """
        INSERT INTO credentials (id, is_active, created_at, updated_at, tenant_id,
                                 provider_type, encrypted_credential, set_by_user_id,
                                 validated_at)
        SELECT id, is_active, created_at, updated_at, organization_id,
               provider_type, encrypted_credential, set_by_user_id, validated_at
        FROM organization_credentials
        """
    )
    op.drop_table("organization_credentials")
    op.drop_table("user_credentials")


def downgrade() -> None:
    """Downgrade schema (best-effort -- see the module docstring)."""
    # --- credentials -> user_credentials + organization_credentials -------
    op.create_table(
        "user_credentials",
        *_base_columns(),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider_type", sa.String(length=32), nullable=False),
        sa.Column("encrypted_credential", sa.Text(), nullable=False),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "provider_type", name="uq_user_credentials_user_provider"),
    )
    op.create_index("ix_user_credentials_user_id", "user_credentials", ["user_id"])
    op.execute(
        """
        INSERT INTO user_credentials (id, is_active, created_at, updated_at, user_id,
                                      provider_type, encrypted_credential, validated_at)
        SELECT id, is_active, created_at, updated_at, user_id,
               provider_type, encrypted_credential, validated_at
        FROM credentials WHERE user_id IS NOT NULL
        """
    )

    # --- tenant_id -> organization_id (invitations, documents, users) -----
    op.execute(
        "ALTER TABLE invitations RENAME CONSTRAINT invitations_tenant_id_fkey "
        "TO invitations_organization_id_fkey"
    )
    op.execute("ALTER INDEX ix_invitations_tenant_id RENAME TO ix_invitations_organization_id")
    op.alter_column("invitations", "tenant_id", new_column_name="organization_id")

    op.execute(
        "ALTER TABLE documents RENAME CONSTRAINT uq_documents_tenant_content_hash "
        "TO uq_documents_organization_content_hash"
    )
    op.execute(
        "ALTER TABLE documents RENAME CONSTRAINT documents_tenant_id_fkey "
        "TO documents_organization_id_fkey"
    )
    op.execute("ALTER INDEX ix_documents_tenant_id RENAME TO ix_documents_organization_id")
    op.alter_column("documents", "tenant_id", new_column_name="organization_id")

    op.execute("ALTER TABLE users RENAME CONSTRAINT fk_users_tenant_id TO fk_users_organization_id")
    op.execute("ALTER INDEX ix_users_tenant_id RENAME TO ix_users_organization_id")
    op.alter_column("users", "tenant_id", new_column_name="organization_id", nullable=False)

    # --- tenants -> organizations (owner_user_id restored from role) ------
    op.execute("ALTER TABLE tenants RENAME CONSTRAINT uq_tenants_domain TO uq_organizations_domain")
    op.execute("ALTER TABLE tenants RENAME CONSTRAINT tenants_name_key TO organizations_name_key")
    op.execute("ALTER TABLE tenants RENAME CONSTRAINT tenants_pkey TO organizations_pkey")
    op.rename_table("tenants", "organizations")
    op.add_column("organizations", sa.Column("owner_user_id", sa.UUID(), nullable=True))
    op.execute(
        """
        UPDATE organizations SET owner_user_id = (
            SELECT u.id FROM users u
            WHERE u.organization_id = organizations.id AND u.role = 'owner'
            ORDER BY u.created_at ASC LIMIT 1
        )
        """
    )
    op.alter_column("organizations", "owner_user_id", nullable=False)
    op.create_foreign_key(
        "fk_organizations_owner_user_id", "organizations", "users", ["owner_user_id"], ["id"]
    )

    op.create_table(
        "organization_credentials",
        *_base_columns(),
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
    op.execute(
        """
        INSERT INTO organization_credentials (id, is_active, created_at, updated_at,
                                              organization_id, provider_type,
                                              encrypted_credential, set_by_user_id,
                                              validated_at)
        SELECT id, is_active, created_at, updated_at, tenant_id, provider_type,
               encrypted_credential, set_by_user_id, validated_at
        FROM credentials
        WHERE tenant_id IS NOT NULL AND set_by_user_id IS NOT NULL
        """
    )
    op.drop_table("credentials")

    # --- organization_members / knowledge_access / usage_counters ---------
    op.create_table(
        "organization_members",
        *_base_columns(),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="member"),
        sa.UniqueConstraint("user_id", name="uq_organization_members_user"),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'member')", name="ck_organization_members_role"
        ),
    )
    op.create_index("ix_organization_members_organization_id", "organization_members", ["organization_id"])
    op.create_index("ix_organization_members_user_id", "organization_members", ["user_id"])
    op.execute(
        """
        INSERT INTO organization_members (id, organization_id, user_id, role)
        SELECT gen_random_uuid(), organization_id, id, role FROM users
        """
    )

    op.create_table(
        "knowledge_access",
        *_base_columns(),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("knowledge_type", sa.String(length=16), nullable=False, server_default="company"),
        sa.UniqueConstraint(
            "organization_id", "user_id", "knowledge_type",
            name="uq_knowledge_access_org_user_type",
        ),
    )
    op.create_index("ix_knowledge_access_organization_id", "knowledge_access", ["organization_id"])
    op.create_index("ix_knowledge_access_user_id", "knowledge_access", ["user_id"])
    op.execute(
        """
        INSERT INTO knowledge_access (id, organization_id, user_id, knowledge_type)
        SELECT gen_random_uuid(), organization_id, id, 'company' FROM users
        WHERE has_company_knowledge_access
        """
    )

    op.create_table(
        "usage_counters",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("documents_uploaded_count", sa.Integer(), nullable=False),
    )
    op.execute(
        """
        INSERT INTO usage_counters (user_id, documents_uploaded_count)
        SELECT id, documents_uploaded_count FROM users
        WHERE documents_uploaded_count > 0
        """
    )

    op.drop_column("users", "documents_uploaded_count")
    op.drop_column("users", "has_company_knowledge_access")

    # --- users.role: tenant role -> old app role --------------------------
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.execute("UPDATE users SET role = 'user'")
    op.create_check_constraint("ck_users_role", "users", "role IN ('admin', 'user')")
