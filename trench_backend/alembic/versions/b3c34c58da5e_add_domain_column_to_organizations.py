"""add domain column to organizations

Revision ID: b3c34c58da5e
Revises: 27c2bef66962
Create Date: 2026-09-10 18:48:16.455930

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3c34c58da5e'
down_revision: Union[str, Sequence[str], None] = '27c2bef66962'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('organizations', sa.Column('domain', sa.String(length=255), nullable=False))
    op.create_unique_constraint('uq_organizations_domain', 'organizations', ['domain'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('uq_organizations_domain', 'organizations', type_='unique')
    op.drop_column('organizations', 'domain')
