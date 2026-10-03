"""refresh openai anthropic google model catalog

Revision ID: 27c2bef66962
Revises: 8281c2609d38
Create Date: 2026-09-10 16:19:45.161790

Replaces the OpenAI/Anthropic/Google rows seeded in
8281c2609d38_remove_local_provider_seed_openai_ with the currently
available model list confirmed by the user (cross-checked against
Anthropic's own model-deprecations page and Google's ai.google.dev models
page during authoring -- OpenAI's docs site returned unreliable/unverifiable
content on fetch, so that list came directly from the user instead of a
live source). Groq is untouched here -- it's refreshed via
ProviderCatalogService.sync_groq_models against Groq's live /models
endpoint, not a static migration.
"""

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "27c2bef66962"
down_revision: Union[str, Sequence[str], None] = "8281c2609d38"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

providers_table = sa.table(
    "providers",
    sa.column("id", sa.UUID),
    sa.column("name", sa.String),
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

_MODELS_BY_PROVIDER = {
    "openai": [
        ("gpt-4o-mini", "GPT-4o Mini"),
        ("gpt-4o", "GPT-4o"),
        ("gpt-4.1-nano", "GPT-4.1 Nano"),
        ("gpt-4.1-mini", "GPT-4.1 Mini"),
        ("gpt-4.1", "GPT-4.1"),
        ("gpt-5-nano", "GPT-5 Nano"),
        ("gpt-5-mini", "GPT-5 Mini"),
        ("gpt-5", "GPT-5"),
        ("gpt-5-pro", "GPT-5 Pro"),
    ],
    "anthropic": [
        ("claude-opus-4-5-20251101", "Claude Opus 4.5"),
        ("claude-sonnet-4-5-20250929", "Claude Sonnet 4.5"),
        ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
        ("claude-opus-4-6", "Claude Opus 4.6"),
        ("claude-opus-4-7", "Claude Opus 4.7"),
        ("claude-opus-4-8", "Claude Opus 4.8"),
        ("claude-haiku-4-5-20251001", "Claude Haiku 4.5"),
        ("claude-sonnet-5", "Claude Sonnet 5"),
        ("claude-opus-5", "Claude Opus 5"),
        ("claude-fable-5", "Claude Fable 5"),
    ],
    "google": [
        ("gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite"),
        ("gemini-2.5-flash", "Gemini 2.5 Flash"),
        ("gemini-2.5-pro", "Gemini 2.5 Pro"),
        ("gemini-3-flash-preview", "Gemini 3 Flash Preview"),
        ("gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview"),
        ("gemini-3.1-flash-lite", "Gemini 3.1 Flash Lite"),
        ("gemini-3.5-flash-lite", "Gemini 3.5 Flash Lite"),
        ("gemini-3.5-flash", "Gemini 3.5 Flash"),
        ("gemini-3.6-flash", "Gemini 3.6 Flash"),
    ],
}


def upgrade() -> None:
    connection = op.get_bind()

    for provider_name, models in _MODELS_BY_PROVIDER.items():
        provider_id = connection.execute(
            sa.select(providers_table.c.id).where(
                providers_table.c.name == provider_name
            )
        ).scalar_one()

        # Replace wholesale: none of these providers' models are the
        # platform default (that's always a Groq model), so there's no
        # is_platform_default row to preserve here.
        op.execute(
            provider_models_table.delete().where(
                provider_models_table.c.provider_id == provider_id
            )
        )
        op.bulk_insert(
            provider_models_table,
            [
                {
                    "id": str(uuid.uuid4()),
                    "is_active": True,
                    "provider_id": provider_id,
                    "model_name": model_name,
                    "display_name": display_name,
                    "is_platform_default": False,
                }
                for model_name, display_name in models
            ],
        )


def downgrade() -> None:
    raise NotImplementedError(
        "This migration replaces the OpenAI/Anthropic/Google model catalog "
        "wholesale -- restore from a backup taken before upgrading instead "
        "of downgrading."
    )
