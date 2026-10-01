"""make users organization_id not null

Revision ID: 70ed057f9456
Revises: c1a3fa88c33a
Create Date: 2026-10-01 14:39:58.912555

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '70ed057f9456'
down_revision: Union[str, Sequence[str], None] = 'c1a3fa88c33a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema. Run ONLY after scripts/wipe_all_users.py has been
    run and the invitation-only signup/accept flow is the only way a User
    row is created from here on -- both guarantee every remaining/future
    row has organization_id set. Running this earlier fails on any
    pre-existing row with organization_id IS NULL."""
    op.alter_column("users", "organization_id", nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column("users", "organization_id", nullable=True)
