"""add owner_user_id to organizations

Revision ID: 9312844b31c0
Revises: b3c34c58da5e
Create Date: 2026-09-10 19:14:25.734983

Backfills existing organizations' owner_user_id from their earliest
admin member -- there's no other record of "who created this org" for
rows that predate this column.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9312844b31c0'
down_revision: Union[str, Sequence[str], None] = 'b3c34c58da5e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('organizations', sa.Column('owner_user_id', sa.UUID(), nullable=True))
    op.execute(
        """
        UPDATE organizations
        SET owner_user_id = (
            SELECT om.user_id
            FROM organization_members om
            WHERE om.organization_id = organizations.id AND om.role = 'admin'
            ORDER BY om.created_at ASC
            LIMIT 1
        )
        """
    )
    op.alter_column('organizations', 'owner_user_id', nullable=False)
    op.create_foreign_key(
        'fk_organizations_owner_user_id', 'organizations', 'users', ['owner_user_id'], ['id']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_organizations_owner_user_id', 'organizations', type_='foreignkey')
    op.drop_column('organizations', 'owner_user_id')
