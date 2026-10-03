"""hide backup groq models and prune extra groq catalog entries

Revision ID: 3873b7cda9af
Revises: c1a3fa88c33a
Create Date: 2026-10-01 21:25:37.590914

Per the pruned model-catalog spec: only Claude/OpenAI/Gemini models plus
three named Groq-hosted models should remain (openai/gpt-oss-120b --
already the platform default -- and qwen/qwen3.8-27b, qwen/qwen3.6-27b as
hidden backup/fallback models). Removes the other five Groq rows seeded
by d6c40b2a9a59 (openai/gpt-oss-20b, openai/gpt-oss-safeguard-20b,
groq/compound, groq/compound-mini, allam-2-7b) and adds `is_visible` so
the two Qwen models can stay in the catalog (selectable by id, is_active
unchanged) without appearing in the user-facing GET /config listing.

Branches independently off c1a3fa88c33a (not the still-unapplied
70ed057f9456, see Task 13) so it can be applied without first forcing
through that deferred, unrelated NOT NULL migration.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3873b7cda9af'
down_revision: Union[str, Sequence[str], None] = 'c1a3fa88c33a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_REMOVED_GROQ_MODELS = [
    "openai/gpt-oss-20b",
    "openai/gpt-oss-safeguard-20b",
    "groq/compound",
    "groq/compound-mini",
    "allam-2-7b",
]
_HIDDEN_GROQ_MODELS = ["qwen/qwen3.8-27b", "qwen/qwen3.6-27b"]

provider_models_table = sa.table(
    "provider_models",
    sa.column("id", sa.UUID),
    sa.column("model_name", sa.String),
    sa.column("is_visible", sa.Boolean),
)

users_table = sa.table(
    "users",
    sa.column("model_id", sa.UUID),
)


def upgrade() -> None:
    op.add_column(
        "provider_models",
        sa.Column(
            "is_visible", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )
    # Any user who had selected one of the five removed models falls back
    # to the platform default the same way an unset model_id always has
    # (see LLMClientService.resolve_for_user) -- clear the FK before the
    # row it points at is deleted, rather than leaving a user unable to
    # chat because their previously-selected model vanished.
    op.execute(
        users_table.update()
        .where(
            users_table.c.model_id.in_(
                sa.select(provider_models_table.c.id).where(
                    provider_models_table.c.model_name.in_(_REMOVED_GROQ_MODELS)
                )
            )
        )
        .values(model_id=None)
    )
    op.execute(
        provider_models_table.delete().where(
            provider_models_table.c.model_name.in_(_REMOVED_GROQ_MODELS)
        )
    )
    op.execute(
        provider_models_table.update()
        .where(provider_models_table.c.model_name.in_(_HIDDEN_GROQ_MODELS))
        .values(is_visible=False)
    )


def downgrade() -> None:
    op.execute(
        provider_models_table.update()
        .where(provider_models_table.c.model_name.in_(_HIDDEN_GROQ_MODELS))
        .values(is_visible=True)
    )
    op.drop_column("provider_models", "is_visible")
