"""add parsed_text to documents

Revision ID: a12f57bfe28a
Revises: 5b8e2d7c4a91
Create Date: 2026-10-05 12:00:53.934310

Autogenerate produced an empty diff against the dev DB this was authored
against, because that column had already been added to it by hand ahead
of this migration -- no migration in this history actually creates it, so
a genuinely fresh database would be missing it. `ADD COLUMN IF NOT EXISTS`
makes this correct in both cases: a no-op against that dev DB, and the
real column-creating migration everywhere else (CI, a fresh clone,
production).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a12f57bfe28a"
down_revision: Union[str, Sequence[str], None] = "5b8e2d7c4a91"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS parsed_text TEXT")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS parsed_text")
