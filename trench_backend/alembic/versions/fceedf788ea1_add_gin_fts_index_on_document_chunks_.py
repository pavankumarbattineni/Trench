"""add gin fts index on document_chunks content

Revision ID: fceedf788ea1
Revises: 6564d8332a33
Create Date: 2026-09-10 14:58:18.247763

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fceedf788ea1"
down_revision: Union[str, Sequence[str], None] = "6564d8332a33"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_document_chunks_content_fts ON document_chunks "
        "USING GIN (to_tsvector('english', content))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ix_document_chunks_content_fts")
