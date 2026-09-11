"""remove firebase_uid chunking_strategies document_chunks embedding rerank models

Revision ID: 3e4e0e662bda
Revises: fceedf788ea1
Create Date: 2026-09-10 15:46:27.152898

Drops everything superseded by the pivot to: no background-job queue
(procrastinate removed from the project entirely), no Postgres chunk
table (chunk text + vectors now live only in Pinecone), no chunking-
strategy/embedding-model catalog (both are now fixed, code-level choices
-- see ChunkingService/EmbeddingService), and no stored Firebase UID
(Trench correlates a Firebase account to its own user row by email now --
see UserService.get_or_create_user).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3e4e0e662bda"
down_revision: Union[str, Sequence[str], None] = "fceedf788ea1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # procrastinate's own tables were never Alembic-managed to begin with
    # (they were created out-of-band via `procrastinate schema --apply`,
    # see the project history) -- so a fresh database replaying this
    # migration chain from scratch never has them, while an existing dev
    # database that had procrastinate wired up still does. IF EXISTS
    # handles both: background jobs are now in-process asyncio tasks (see
    # DocumentService/ChatService), so this queue is no longer used at all.
    op.execute("DROP TABLE IF EXISTS procrastinate_events CASCADE")
    op.execute("DROP TABLE IF EXISTS procrastinate_periodic_defers CASCADE")
    op.execute("DROP TABLE IF EXISTS procrastinate_jobs CASCADE")
    op.execute("DROP TABLE IF EXISTS procrastinate_workers CASCADE")

    # document_chunks -- superseded by Pinecone-only chunk storage.
    op.drop_index("ix_document_chunks_content_fts", table_name="document_chunks")
    op.drop_index("ix_document_chunks_document_id", table_name="document_chunks")
    op.drop_table("document_chunks")

    # Drop the FK constraints into chunking_strategies/provider_models
    # before dropping chunking_strategies itself (Postgres refuses to
    # drop a table something still references).
    op.drop_constraint(
        "documents_chunk_strategy_id_fkey", "documents", type_="foreignkey"
    )
    op.drop_constraint(
        "documents_embedding_model_id_fkey", "documents", type_="foreignkey"
    )
    op.drop_constraint("users_chunk_splitter_id_fkey", "users", type_="foreignkey")
    op.drop_constraint("users_embedding_model_id_fkey", "users", type_="foreignkey")

    op.drop_index("uq_chunking_strategies_default", table_name="chunking_strategies")
    op.drop_table("chunking_strategies")

    # documents: file_size_bytes -> file_size, chunks_count -> chunk_count,
    # drop chunk_strategy_id/embedding_model_id/chunk_size (all fixed,
    # code-level choices now).
    op.add_column("documents", sa.Column("file_size", sa.Integer(), nullable=True))
    op.execute("UPDATE documents SET file_size = file_size_bytes")
    op.alter_column("documents", "file_size", nullable=False)
    op.add_column(
        "documents",
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute("UPDATE documents SET chunk_count = chunks_count")
    op.alter_column("documents", "chunk_count", server_default=None)
    op.drop_column("documents", "file_size_bytes")
    op.drop_column("documents", "embedding_model_id")
    op.drop_column("documents", "chunk_strategy_id")
    op.drop_column("documents", "chunk_size")
    op.drop_column("documents", "chunks_count")

    # users: drop firebase_uid (correlated by email now), embedding_model_id
    # and chunk_splitter_id (fixed, code-level choices now).
    op.drop_index("ix_users_firebase_uid", table_name="users")
    op.drop_column("users", "firebase_uid")
    op.drop_column("users", "chunk_splitter_id")
    op.drop_column("users", "embedding_model_id")

    # provider_models: only LLM models are cataloged now -- drop the
    # model_type discriminator and the embedding-only "dimensions" column.
    # The old "one default per model_type" index is dropped here but its
    # single-default replacement isn't created until the next migration,
    # after the (still is_platform_default=True) local embedding/rerank
    # rows are deleted -- otherwise multiple True rows would violate the
    # new unique index immediately.
    op.drop_index("uq_provider_models_default_per_type", table_name="provider_models")
    op.drop_column("provider_models", "dimensions")
    op.drop_column("provider_models", "model_type")


def downgrade() -> None:
    raise NotImplementedError(
        "This migration drops the procrastinate/chunking_strategies/"
        "document_chunks tables and the firebase_uid column outright -- "
        "restore from a backup taken before upgrading instead of downgrading."
    )
