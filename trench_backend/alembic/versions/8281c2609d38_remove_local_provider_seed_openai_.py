"""remove local provider seed openai anthropic google models

Revision ID: 8281c2609d38
Revises: 3e4e0e662bda
Create Date: 2026-09-10 15:47:22.248458

Removes the "local" provider and its embedding/rerank ProviderModel rows
(embedding/rerank are now fixed, code-level choices -- see
EmbeddingService -- not a database catalog), and seeds the LLM catalog
with OpenAI, Anthropic, and Google alongside the existing Groq rows.

The exact model name strings for OpenAI/Anthropic/Google below are a
reasonable, curated snapshot as of this migration's authoring -- unlike
Groq's list (verified live against Groq's own /models endpoint), there is
no live-verified source for these three here. Check each provider's own
model list before relying on one of these in production; a stale/retired
model id will surface as a real BYOK generation failure for that model,
not a silent one.
"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8281c2609d38"
down_revision: Union[str, Sequence[str], None] = "3e4e0e662bda"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

providers_table = sa.table(
    "providers",
    sa.column("id", sa.UUID),
    sa.column("is_active", sa.Boolean),
    sa.column("name", sa.String),
    sa.column("display_name", sa.String),
)

provider_models_table = sa.table(
    "provider_models",
    sa.column("id", sa.UUID),
    sa.column("is_active", sa.Boolean),
    sa.column("provider_id", sa.UUID),
    sa.column("model_name", sa.String),
    sa.column("display_name", sa.String),
    sa.column("is_platform_default", sa.Boolean),
)

_NEW_PROVIDERS = {
    "openai": (
        "OpenAI",
        [
            ("gpt-5", "GPT-5"),
            ("gpt-5-mini", "GPT-5 Mini"),
            ("gpt-4.1", "GPT-4.1"),
            ("gpt-4o", "GPT-4o"),
            ("gpt-4o-mini", "GPT-4o Mini"),
            ("o3-mini", "o3-mini"),
        ],
    ),
    "anthropic": (
        "Anthropic",
        [
            ("claude-opus-4-1-20250805", "Claude Opus 4.1"),
            ("claude-sonnet-4-5-20250929", "Claude Sonnet 4.5"),
            ("claude-haiku-4-5-20251001", "Claude Haiku 4.5"),
            ("claude-3-5-haiku-20241022", "Claude 3.5 Haiku"),
        ],
    ),
    "google": (
        "Google",
        [
            ("gemini-2.5-pro", "Gemini 2.5 Pro"),
            ("gemini-2.5-flash", "Gemini 2.5 Flash"),
            ("gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite"),
            ("gemini-2.0-flash", "Gemini 2.0 Flash"),
        ],
    ),
}


def upgrade() -> None:
    connection = op.get_bind()

    local_provider_id = connection.execute(
        sa.select(providers_table.c.id).where(providers_table.c.name == "local")
    ).scalar_one_or_none()
    if local_provider_id is not None:
        op.execute(
            provider_models_table.delete().where(
                provider_models_table.c.provider_id == local_provider_id
            )
        )
        op.execute(
            providers_table.delete().where(providers_table.c.id == local_provider_id)
        )

    # Only now (after the local embedding/rerank rows -- both
    # is_platform_default=True -- are gone) can "at most one default,
    # period" be enforced without an immediate unique-index violation.
    op.create_index(
        "uq_provider_models_default",
        "provider_models",
        ["is_platform_default"],
        unique=True,
        postgresql_where=sa.text("is_platform_default"),
    )

    for provider_name, (display_name, models) in _NEW_PROVIDERS.items():
        provider_id = str(uuid.uuid4())
        op.bulk_insert(
            providers_table,
            [
                {
                    "id": provider_id,
                    "is_active": True,
                    "name": provider_name,
                    "display_name": display_name,
                }
            ],
        )
        op.bulk_insert(
            provider_models_table,
            [
                {
                    "id": str(uuid.uuid4()),
                    "is_active": True,
                    "provider_id": provider_id,
                    "model_name": model_name,
                    "display_name": model_display_name,
                    "is_platform_default": False,
                }
                for model_name, model_display_name in models
            ],
        )


def downgrade() -> None:
    raise NotImplementedError(
        "This migration deletes the local provider's rows outright -- "
        "restore from a backup taken before upgrading instead of downgrading."
    )
