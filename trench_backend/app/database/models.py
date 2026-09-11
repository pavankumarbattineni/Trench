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
from sqlalchemy.dialects.postgresql import JSONB, UUID
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
    """No firebase_uid: Firebase still owns identity/passwords, but a
    Trench user row is correlated to a Firebase account by email (see
    UserService.create_user), not by storing Firebase's own UID."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    username: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    # The Trench *application* role -- "admin" | "user". Entirely separate
    # from OrganizationMember.role (an org's own admin/member roles): a
    # Trench admin can create organizations but isn't automatically an
    # admin of any one of them, and vice versa (see UserService.create_user
    # for how "admin" can ever be assigned -- never from a raw client value).
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    # The user's currently selected LLM, surfaced through GET /users/me.
    # Nullable: resolved lazily to the platform default the first time it
    # matters (see UserPreferenceService) rather than being required at
    # signup. Embedding/chunking are fixed, code-level choices now (see
    # EmbeddingService/ChunkingService) -- not user-selectable, so there's
    # no equivalent column for either.
    model_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("provider_models.id"), nullable=True
    )


class Organization(BaseModel):
    """An organization's identity is its email domain (see
    app/utils/email_domain.py) -- derived from its creator's email at
    creation time, never user-entered directly, and used to gate which
    users can subsequently be added as members."""

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    domain: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    # The creator, permanently -- unlike role="admin" (which any admin can
    # grant/revoke on any other member), the owner can never be changed,
    # downgraded, or removed by anyone, including other admins. See
    # OrganizationService.require_not_owner.
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )


class OrganizationMember(BaseModel):
    """A user's membership in an organization.

    A user belongs to at most one organization (enforced by the unique
    constraint on user_id alone), so "the user's organization" is always an
    unambiguous single lookup -- no need to disambiguate which org a
    `knowledge_type=company` request refers to.
    """

    __tablename__ = "organization_members"
    __table_args__ = (UniqueConstraint("user_id", name="uq_organization_members_user"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # "admin" | "member"
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="member")


class KnowledgeAccess(BaseModel):
    """Grants a user permission to query an organization's company knowledge.

    Membership in an organization does not itself grant company-knowledge
    access -- that must be explicitly granted by an org admin. The
    knowledge_type column is forward-looking (currently always "company";
    personal knowledge needs no grant since ownership is the check).
    """

    __tablename__ = "knowledge_access"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "user_id",
            "knowledge_type",
            name="uq_knowledge_access_org_user_type",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    knowledge_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="company"
    )


class Document(BaseModel):
    """No chunk_strategy_id/embedding_model_id/chunk_size: chunking and
    embedding are fixed, code-level choices (see ChunkingService,
    EmbeddingService), not per-document configuration. No chunk rows
    either -- each chunk's text and dense+sparse vectors live only in
    Pinecone (see VectorStoreService), keyed by a deterministic
    f"{document_id}:{chunk_index}" id so a delete can reconstruct exactly
    which vectors to purge from `chunk_count` alone.
    """

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "content_hash", name="uq_documents_user_content_hash"
        ),
        # Ignored for personal documents (organization_id is NULL there, and
        # Postgres treats NULLs as distinct) -- this only dedupes company
        # documents re-uploaded by a different org admin than the original
        # uploader, which the per-user constraint above wouldn't catch.
        UniqueConstraint(
            "organization_id",
            "content_hash",
            name="uq_documents_organization_content_hash",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Set only for knowledge_type="company" documents -- the organization
    # this document's company knowledge belongs to (and the vector store
    # namespace it's indexed under). NULL for personal documents.
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    document_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Friendly category derived from the sniffed content type at upload
    # time ("pdf" | "docx" | "txt" | "md") -- mime_type is kept alongside
    # it for storage/serving purposes.
    document_type: Mapped[str] = mapped_column(String(16), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "company" | "personal"
    knowledge_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="personal"
    )
    # "default" (the org's shared company base) | "own" (a user's personal
    # base). Currently fully determined by knowledge_type (company->default,
    # personal->own) but kept as its own column so future sub-scopes (e.g.
    # per-department company bases) don't require a Document schema change.
    knowledge_base: Mapped[str] = mapped_column(
        String(16), nullable=False, default="own"
    )
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


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
    """A specific chat/LLM model offered by a Provider.

    All LLM configuration Trench's code needs (which model is the platform
    default, etc.) is read from this table -- never hardcoded as a string
    literal in application code. Embedding/rerank models are NOT stored
    here -- both are fixed, code-level choices (see EmbeddingService),
    since Trench doesn't offer per-user embedding/rerank selection.
    """

    __tablename__ = "provider_models"
    __table_args__ = (
        UniqueConstraint(
            "provider_id", "model_name", name="uq_provider_models_provider_model"
        ),
        # At most one platform default (the partial index only covers rows
        # where is_platform_default is true, so it enforces "one default"
        # rather than global uniqueness). Only "llm" models exist in this
        # table now, so there's no need to scope the uniqueness by type.
        Index(
            "uq_provider_models_default",
            "is_platform_default",
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
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
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
    # openai_llm | anthropic_llm | gemini_llm | pinecone
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False)
    encrypted_credential: Mapped[str] = mapped_column(Text, nullable=False)
    validated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class Thread(BaseModel):
    __tablename__ = "threads"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)


class ChatHistory(BaseModel):
    __tablename__ = "chat_history"

    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("threads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # "user" | "assistant"
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Retrieved chunks/citation metadata behind this response -- e.g.
    # [{"document_id", "document_name", "chunk_index", "content", "score"}].
    # Empty for user-role rows.
    chunks: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # "pending" | "running" | "completed" | "failed" | "interrupted"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="completed")
