"""seed provider catalog: local embedding/rerank defaults and groq llm models

Revision ID: d6c40b2a9a59
Revises: 6ef98a1176c7
Create Date: 2026-09-10 13:00:15.037196

This is a real snapshot verified against Groq's live /models endpoint on
2026-09-10, filtered to text-in/text-out chat-capable models (excluding
Whisper transcription, Orpheus text-to-speech, and Llama Prompt Guard
classifier models). Refreshable later via
ProviderCatalogService.sync_groq_models() without another migration.
"""

import uuid

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d6c40b2a9a59"
down_revision: str | None = "6ef98a1176c7"
branch_labels: str | None = None
depends_on: str | None = None

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
    sa.column("model_type", sa.String),
    sa.column("model_name", sa.String),
    sa.column("display_name", sa.String),
    sa.column("dimensions", sa.Integer),
    sa.column("is_platform_default", sa.Boolean),
)

_GROQ_CHAT_MODELS = [
    ("openai/gpt-oss-120b", "GPT OSS 120B", True),
    ("openai/gpt-oss-20b", "GPT OSS 20B", False),
    ("openai/gpt-oss-safeguard-20b", "Safety GPT OSS 20B", False),
    ("groq/compound", "Compound", False),
    ("groq/compound-mini", "Compound Mini", False),
    ("qwen/qwen3.8-27b", "Qwen/Qwen3.8-27B", False),
    ("qwen/qwen3.6-27b", "Qwen/Qwen3.6-27B", False),
    ("allam-2-7b", "ALLaM-2-7b", False),
]


def upgrade() -> None:
    local_id = str(uuid.uuid4())
    groq_id = str(uuid.uuid4())

    op.bulk_insert(
        providers_table,
        [
            {
                "id": local_id,
                "is_active": True,
                "name": "local",
                "display_name": "Local",
            },
            {"id": groq_id, "is_active": True, "name": "groq", "display_name": "Groq"},
        ],
    )

    op.bulk_insert(
        provider_models_table,
        [
            {
                "id": str(uuid.uuid4()),
                "is_active": True,
                "provider_id": local_id,
                "model_type": "embedding",
                "model_name": "BAAI/bge-small-en-v1.5",
                "display_name": "BGE Small (local)",
                "dimensions": 384,
                "is_platform_default": True,
            },
            {
                "id": str(uuid.uuid4()),
                "is_active": True,
                "provider_id": local_id,
                "model_type": "rerank",
                "model_name": "cross-encoder/ms-marco-MiniLM-L-6-v2",
                "display_name": "MS MARCO MiniLM Cross-Encoder (local)",
                "dimensions": None,
                "is_platform_default": True,
            },
            *(
                {
                    "id": str(uuid.uuid4()),
                    "is_active": True,
                    "provider_id": groq_id,
                    "model_type": "llm",
                    "model_name": model_name,
                    "display_name": display_name,
                    "dimensions": None,
                    "is_platform_default": is_default,
                }
                for model_name, display_name, is_default in _GROQ_CHAT_MODELS
            ),
        ],
    )


def downgrade() -> None:
    op.execute(
        provider_models_table.delete().where(
            provider_models_table.c.model_name.in_(
                [name for name, _, _ in _GROQ_CHAT_MODELS]
                + ["BAAI/bge-small-en-v1.5", "cross-encoder/ms-marco-MiniLM-L-6-v2"]
            )
        )
    )
    op.execute(
        providers_table.delete().where(providers_table.c.name.in_(["local", "groq"]))
    )
