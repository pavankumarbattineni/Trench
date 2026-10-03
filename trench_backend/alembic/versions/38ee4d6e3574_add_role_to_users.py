"""add role to users

Revision ID: 38ee4d6e3574
Revises: 9312844b31c0
Create Date: 2026-09-11 10:22:38.210779

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '38ee4d6e3574'
down_revision: Union[str, Sequence[str], None] = '9312844b31c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'users',
        sa.Column(
            'role', sa.String(length=16), nullable=False, server_default='user'
        ),
    )
    op.alter_column('users', 'role', server_default=None)
    op.create_check_constraint(
        'ck_users_role', 'users', "role IN ('admin', 'user')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('ck_users_role', 'users', type_='check')
    op.drop_column('users', 'role')
