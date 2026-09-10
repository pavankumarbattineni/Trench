import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class BaseModel(Base):
    """Shared columns for top-level entities: id, soft-disable flag, timestamps."""

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class User(BaseModel):
    __tablename__ = "users"

    firebase_uid: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    username: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )


class KnowledgeBase(BaseModel):
    __tablename__ = "knowledge_bases"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(128), nullable=False, default="My Knowledge Base"
    )
    embedding_provider: Mapped[str] = mapped_column(
        String(32), nullable=False, default="local"
    )
    embedding_model: Mapped[str] = mapped_column(
        String(128), nullable=False, default="BAAI/bge-small-en-v1.5"
    )
    embedding_dimensions: Mapped[int] = mapped_column(
        Integer, nullable=False, default=384
    )
    # Pinecone for both tiers -- operator-owned project/index for free
    # users, the user's own credentials/index once they BYOK. See
    # docs/superpowers/specs/2026-09-10-agentic-rag-design.md.
    vector_store_provider: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pinecone"
    )
    vector_store_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Document(BaseModel):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "content_hash", name="uq_documents_user_content_hash"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunking_strategy: Mapped[str] = mapped_column(
        String(32), nullable=False, default="recursive"
    )
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False, default=512)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DocumentChunk(BaseModel):
    __tablename__ = "document_chunks"

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # No embedding column -- the vector lives only in Pinecone, keyed by
    # this row's own id. Postgres never stores embeddings.


class UsageCounter(Base):
    __tablename__ = "usage_counters"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    documents_uploaded_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )


class Provider(BaseModel):
    __tablename__ = "providers"

    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)


class ProviderModel(BaseModel):
    """A specific model offered by a Provider.

    All model configuration Trench's code needs (which model is the
    platform default for a given purpose, embedding dimensions, etc.) is
    read from this table -- never hardcoded as a string literal in
    application code.
    """

    __tablename__ = "provider_models"
    __table_args__ = (
        UniqueConstraint(
            "provider_id", "model_name", name="uq_provider_models_provider_model"
        ),
        # At most one platform default per model_type (the partial index
        # only covers rows where is_platform_default is true, so it
        # enforces "one default per type" rather than global uniqueness).
        Index(
            "uq_provider_models_default_per_type",
            "model_type",
            unique=True,
            postgresql_where=text("is_platform_default"),
        ),
    )

    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # "llm" | "embedding" | "rerank" | "parsing"
    model_type: Mapped[str] = mapped_column(String(16), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    dimensions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_platform_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class UserCredential(BaseModel):
    __tablename__ = "user_credentials"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "provider_type", name="uq_user_credentials_user_provider"
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # llamaparse | openai_embed | openai_llm | anthropic_llm | gemini_llm |
    # cohere_rerank | pinecone
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False)
    encrypted_credential: Mapped[str] = mapped_column(Text, nullable=False)
    validated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
