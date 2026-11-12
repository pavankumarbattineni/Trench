"""add llm_usage.turn_id

Groups one question's LLM calls without a foreign key, so deleting a chat
(which sets assistant_message_id to NULL) no longer drops its calls from
the per-question usage list while they still count in the totals.

Revision ID: b7c1e2d3f4a5
Revises: ab0d9d0fa61d
Create Date: 2026-11-12 15:00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7c1e2d3f4a5"
down_revision: Union[str, Sequence[str], None] = "ab0d9d0fa61d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("llm_usage", sa.Column("turn_id", sa.UUID(), nullable=True))
    op.create_index("ix_llm_usage_turn_id", "llm_usage", ["turn_id"])
    # Calls whose answer still exists: the turn is that answer.
    op.execute(
        "UPDATE llm_usage SET turn_id = assistant_message_id "
        "WHERE assistant_message_id IS NOT NULL"
    )
    # Calls from already-deleted chats: one question's calls share the
    # user, question text and minute -- give each such group its own id.
    op.execute(
        """
        WITH groups AS (
            SELECT user_id, query_preview,
                   date_trunc('minute', created_at) AS minute,
                   gen_random_uuid() AS turn
            FROM llm_usage
            WHERE turn_id IS NULL
            GROUP BY user_id, query_preview, date_trunc('minute', created_at)
        )
        UPDATE llm_usage u SET turn_id = g.turn
        FROM groups g
        WHERE u.turn_id IS NULL
          AND u.user_id = g.user_id
          AND u.query_preview = g.query_preview
          AND date_trunc('minute', u.created_at) = g.minute
        """
    )


def downgrade() -> None:
    op.drop_index("ix_llm_usage_turn_id", table_name="llm_usage")
    op.drop_column("llm_usage", "turn_id")
