"""seed chunking strategies catalog

Revision ID: 6564d8332a33
Revises: 13acffaf49db
Create Date: 2026-09-10 14:58:00.772695

"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6564d8332a33"
down_revision: Union[str, Sequence[str], None] = "13acffaf49db"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

chunking_strategies_table = sa.table(
    "chunking_strategies",
    sa.column("id", sa.UUID),
    sa.column("is_active", sa.Boolean),
    sa.column("name", sa.String),
    sa.column("display_name", sa.String),
    sa.column("description", sa.Text),
    sa.column("is_platform_default", sa.Boolean),
)

_STRATEGIES = [
    (
        "recursive",
        "Recursive Character Splitter",
        "Splits on paragraph/sentence/word boundaries, recursively, to "
        "pack chunks close to the target size without cutting mid-word. "
        "A solid general-purpose default for prose.",
        True,
    ),
    (
        "markdown",
        "Markdown-Aware Splitter",
        "Splits along Markdown structure (headers, code fences, lists) "
        "before falling back to character boundaries -- keeps a section "
        "together rather than cutting across a heading.",
        False,
    ),
    (
        "token",
        "Token-Based Splitter",
        "Splits on a fixed token-count window (via tiktoken), independent "
        "of prose structure -- useful when staying under a strict "
        "embedding/LLM context budget matters more than semantic boundaries.",
        False,
    ),
    (
        "semantic",
        "Semantic (Paragraph-Grouping) Splitter",
        "Greedily groups whole paragraphs up to the target token budget, "
        "never splitting a paragraph mid-way -- a lightweight approximation "
        "of topic-boundary-aware chunking.",
        False,
    ),
]


def upgrade() -> None:
    op.bulk_insert(
        chunking_strategies_table,
        [
            {
                "id": str(uuid.uuid4()),
                "is_active": True,
                "name": name,
                "display_name": display_name,
                "description": description,
                "is_platform_default": is_default,
            }
            for name, display_name, description, is_default in _STRATEGIES
        ],
    )


def downgrade() -> None:
    op.execute(
        chunking_strategies_table.delete().where(
            chunking_strategies_table.c.name.in_([name for name, *_ in _STRATEGIES])
        )
    )
