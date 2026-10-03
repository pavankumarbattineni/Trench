import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
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
    UserService.create_user), not by storing Firebase's own UID.

    A user's tenant and their role in it live directly on this row
    (`tenant_id` + `role`) -- there is no separate membership join table,
    so "which tenant is this user in, and what's their role" is always a
    single, join-free read (see docs/superpowers/specs/
    2026-10-03-tenant-rbac-redesign-design.md). The same goes for their
    company-knowledge grant and free-tier usage counter, which used to be
    their own tables.
    """

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'admin', 'member')", name="ck_users_role"),
    )

    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    username: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    # The user's role *within their tenant* -- "owner" | "admin" |
    # "member". There is no separate Trench-wide application role anymore.
    # Exactly one "owner" exists per tenant (its creator, set once at
    # signup by TenantService.create) and it can never be changed, demoted,
    # or removed by anyone -- enforced at the application layer only
    # (TenantService.require_not_owner), deliberately not by any DB
    # constraint. An invitation can never carry "owner" either (see
    # InvitationService.create).
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="member")
    # The user's currently selected LLM, surfaced through GET /users/me.
    # Nullable: resolved lazily to the platform default the first time it
    # matters (see UserPreferenceService) rather than being required at
    # signup. Embedding/chunking are fixed, code-level choices now (see
    # EmbeddingService/ChunkingService) -- not user-selectable, so there's
    # no equivalent column for either.
    model_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("provider_models.id"), nullable=True
    )
    # The single tenant this user belongs to -- the only tenant lookup
    # every authorization check uses (always scoped together with `role`).
    # Set by both onboarding paths (TenantService.create for an Owner,
    # InvitationService.accept for an invited Admin/Member). Nullable only
    # because removing a member from their tenant (TenantService.
    # remove_member / remove_all_members) clears it rather than deleting
    # the user's account -- and their personal knowledge -- outright.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True, index=True
    )
    # Whether this user has been explicitly granted read/query access to
    # their tenant's company knowledge. Belonging to a tenant does not
    # itself grant it -- an Admin/Owner must (InvitationService.accept does
    # so by default for an invited "member"). Irrelevant for "admin"/
    # "owner", whose access is always derived from their role instead --
    # see KnowledgeAccessService.has_company_access, the single place that
    # combines the two. Reset to false whenever the user leaves a tenant.
    has_company_knowledge_access: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    # Total personal documents this user has ever had successfully
    # ingested -- the free-tier limit counter (see DocumentService.
    # _enforce_free_tier_limit). Never decremented on delete: the limit is
    # on documents ever processed, not documents currently stored.
    documents_uploaded_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )


class Tenant(BaseModel):
    """A tenant's identity is its email domain (see
    app/utils/email_domain.py) -- derived from its creator's email at
    creation time, never user-entered directly, and used to gate which
    users can subsequently be invited as members.

    There is deliberately no owner column: ownership is `User.role ==
    "owner"` scoped to `User.tenant_id` (see TenantService.get_owner).
    """

    __tablename__ = "tenants"
    __table_args__ = (UniqueConstraint("domain", name="uq_tenants_domain"),)

    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)


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
        # Ignored for personal documents (tenant_id is NULL there, and
        # Postgres treats NULLs as distinct) -- this only dedupes company
        # documents re-uploaded by a different tenant admin than the
        # original uploader, which the per-user constraint above wouldn't
        # catch.
        UniqueConstraint(
            "tenant_id",
            "content_hash",
            name="uq_documents_tenant_content_hash",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Set only for knowledge_type="company" documents -- the tenant this
    # document's company knowledge belongs to (and the vector store
    # namespace it's indexed under). NULL for personal documents.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
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
    # "default" (the tenant's shared company base) | "own" (a user's
    # personal base). Currently fully determined by knowledge_type
    # (company->default, personal->own) but kept as its own column so
    # future sub-scopes (e.g. per-department company bases) don't require a
    # Document schema change.
    knowledge_base: Mapped[str] = mapped_column(
        String(16), nullable=False, default="own"
    )
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
    # False for a model kept in the catalog as an internal backup/fallback
    # only (e.g. the Qwen models) -- still is_active and selectable by id,
    # just excluded from the user-facing GET /config listing.
    is_visible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Credential(BaseModel):
    """A validated, encrypted BYOK credential owned by exactly one of a
    user (personal scope) or a tenant (tenant scope) -- never both, never
    neither (ck_credentials_single_owner).

    - Personal (`user_id` set): the user's own key, used only for their
      personal-knowledge queries and never shared.
    - Tenant (`tenant_id` set): the Owner's key, used on behalf of every
      member for company-knowledge queries. Only ever set/updated by the
      tenant's Owner -- enforced at the router layer, not by any DB
      constraint. `set_by_user_id` records who set it (always NULL for
      personal rows; nulled out, not cascaded, if that user is deleted).

    How each scope is *resolved* at query time differs on purpose (a
    company query never falls back to the querying member's personal key,
    and silently falls back to the platform default instead) -- that's
    LLMClientService.resolve_for_knowledge's job, not this table's.
    """

    __tablename__ = "credentials"
    __table_args__ = (
        CheckConstraint(
            "(user_id IS NULL) <> (tenant_id IS NULL)",
            name="ck_credentials_single_owner",
        ),
        # One credential per provider per owner. Partial, so each index
        # only ever covers its own scope's rows.
        Index(
            "uq_credentials_user_provider",
            "user_id",
            "provider_type",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "uq_credentials_tenant_provider",
            "tenant_id",
            "provider_type",
            unique=True,
            postgresql_where=text("tenant_id IS NOT NULL"),
        ),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # openai_llm | anthropic_llm | gemini_llm | pinecone
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False)
    encrypted_credential: Mapped[str] = mapped_column(Text, nullable=False)
    set_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
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


class Invitation(BaseModel):
    """An email invitation to join a tenant with a specific role.

    The raw token is emailed once and never stored -- only its sha256
    hash (token_hash) is persisted, the same "secrets are never stored in
    plaintext" rule BYOK credentials already follow. Role is fixed at
    invite time by the inviter (never chosen by the invitee at accept
    time) and is always "admin" or "member" -- an invitation can never
    carry role="owner" (see InvitationService.create).
    """

    __tablename__ = "invitations"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_invitations_token_hash"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # "admin" | "member" -- never "owner"
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="member")
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # "pending" | "accepted" | "revoked" | "expired"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    invited_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PasswordResetToken(BaseModel):
    """A single-use, time-limited password reset token, replacing
    reliance on Firebase's own oobCode flow -- see
    PasswordResetService. Same "store only the hash" rule as Invitation.
    """

    __tablename__ = "password_reset_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_password_reset_tokens_token_hash"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
