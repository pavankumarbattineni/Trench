"""multi tenant rbac schema foundation

Revision ID: c1a3fa88c33a
Revises: 38ee4d6e3574
Create Date: 2026-10-01 12:25:50.556486

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c1a3fa88c33a'
down_revision: Union[str, Sequence[str], None] = '38ee4d6e3574'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
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
