from functools import lru_cache
from urllib.parse import quote_plus

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class DBConfig(BaseModel):
    username: str
    password: str
    database: str
    ip_address: str
    port: str
    pool_size: int = 10
    max_overflow: int = 5
    pool_timeout: int = 30
    pool_recycle: int = 1800
    # Require an encrypted (TLS) connection. Off for local Postgres/Compose;
    # on for hosted databases such as Neon, which refuse plain connections.
    ssl: bool = False

    def _base_url(self, scheme: str) -> str:
        # quote_plus: a password containing @ : / % etc. would otherwise
        # break the URL.
        return (
            f"{scheme}://{quote_plus(self.username)}:{quote_plus(self.password)}"
            f"@{self.ip_address}:{self.port}/{self.database}"
        )

    @property
    def url(self) -> str:
        """SQLAlchemy URL (asyncpg driver) -- used by the app and Alembic."""
        url = self._base_url("postgresql+asyncpg")
        return f"{url}?ssl=require" if self.ssl else url

    @property
    def conninfo(self) -> str:
        """libpq/psycopg connection string -- used by the LangGraph
        checkpointer. psycopg spells the SSL option `sslmode`."""
        url = self._base_url("postgresql")
        return f"{url}?sslmode=require" if self.ssl else url


class JWTConfig(BaseModel):
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30


class EncryptionConfig(BaseModel):
    """Root secret used to derive per-purpose Fernet keys for encrypting
    BYOK credentials at rest (see app/utils/encryption.py). Named for what
    it actually is -- not "MCP" (Model Context Protocol), which this has
    nothing to do with."""

    encryption_key: str


class FirebaseConfig(BaseModel):
    # The service-account key's raw file contents (Project settings >
    # Service accounts > Generate new private key), base64-encoded and
    # pasted directly into this config rather than kept as a separate
    # file on disk -- no environment this app runs in should depend on a
    # local file outside the repo existing at a specific path. Base64,
    # not plain JSON: the key's PEM-formatted private_key field is
    # multi-line, and a raw newline (escaped or not) inside a single
    # quoted .env value is exactly the kind of thing a naive .env/JSON
    # parse can silently mangle; base64 has no characters either layer
    # treats specially, so there's nothing to misparse.
    credentials_json_base64: str
    # Firebase's public Web API key (Project settings > General), NOT a
    # secret like the Admin SDK's service-account credentials -- it's
    # required to call the Identity Toolkit REST API's
    # accounts:signInWithPassword endpoint, which is the only way to
    # verify a plaintext password server-side (the Admin SDK can only
    # look up/overwrite users, never check a password against one).
    # See app/utils/firebase.py's verify_user_password.
    web_api_key: str


class GroqConfig(BaseModel):
    api_key: str
    # Groq counts prompt tokens PLUS the requested output limit against the
    # key's tokens-per-minute quota (8,000 on the free tier); a request over
    # it fails with 413. LLMClientService sizes each answer's output limit
    # to fit the budget -- raise this after upgrading the Groq plan.
    tokens_per_minute: int = 8000


class StorageConfig(BaseModel):
    # Legacy: no longer read by get_storage_provider() now that document
    # bytes live in Backblaze B2 (see BackblazeConfig). Kept so existing
    # .env files still validate until it's removed in a follow-up.
    local_root_path: str


class BackblazeConfig(BaseModel):
    """Backblaze B2 bucket holding uploaded documents' raw bytes, reached
    through B2's S3-compatible API with boto3 (see
    app/service/document_storage_service.py's B2StorageProvider) rather
    than the native b2sdk. `application_key_id`/`application_key` are a B2
    application key -- they map onto boto3's aws_access_key_id /
    aws_secret_access_key."""

    endpoint_url: str
    region: str
    bucket_name: str
    application_key_id: str
    application_key: str


class PineconeConfig(BaseModel):
    api_key: str
    # Personal knowledge lives in namespace f"personal:{user_id}", company
    # knowledge in f"company:{tenant_id}" -- both inside this one
    # operator-owned index (BYOK users get their own index/project instead,
    # resolved through their personal Credential rather than this config).
    index_name: str = "trench-platform"


class LlamaParseConfig(BaseModel):
    api_key: str


class CohereRerankerConfig(BaseModel):
    """Cohere's hosted Rerank API -- reranks already-retrieved (hybrid
    dense+sparse) chunks against the actual query (see
    app/service/reranker_service.py). Chosen over a local cross-encoder
    to avoid the torch/sentence-transformers dependency weight."""

    api_key: str


class GeminiConfig(BaseModel):
    api_key: str
    # Embedding texts Google allows per minute for this key. Google counts
    # each text in a batched embed_content call toward its quota. The free
    # tier allows 100/minute; raise this after enabling billing on the key
    # (see EmbeddingService, which paces requests to stay under it).
    embed_requests_per_minute: int = 100


class SMTPConfig(BaseModel):
    host: str
    port: int = 587
    username: str
    password: str
    from_address: str
    use_tls: bool = True


class GmailApiConfig(BaseModel):
    """Sends as a Gmail account through Google's Gmail API over HTTPS. Used
    instead of SMTP when set: many hosts (Render's free tier included) block
    outbound SMTP ports 25/465/587, while HTTPS (443) is always open.

    The OAuth client (client_id/secret) belongs to a Google Cloud project
    with the Gmail API enabled; `refresh_token` comes from authorizing that
    client once, as `from_address`, with the gmail.send scope."""

    client_id: str
    client_secret: str
    refresh_token: str
    from_address: str
    from_name: str = "Trench"


class TrenchConfig(BaseModel):
    ENVIRONMENT: str = "DEV"
    # The frontend's own origin -- used to build links that must point at
    # it rather than the API (invitation-accept, password-reset). Defaults
    # to the local Next.js dev server so a fresh checkout works out of the
    # box; set to the real app domain in staging/prod via TRENCH_CONFIG.
    # No trailing slash (see InvitationService.create/PasswordResetService
    # .request, which join it directly with a leading-slash path).
    FRONTEND_BASE_URL: str = "http://localhost:3000"
    # Browser origins allowed to call the API (CORS). Unset = just
    # FRONTEND_BASE_URL, which covers the usual one-frontend setup; list
    # extra origins here (e.g. a custom domain alongside *.vercel.app).
    CORS_ORIGINS: list[str] | None = None
    DB: DBConfig
    JWT: JWTConfig
    ENCRYPTION: EncryptionConfig
    FIREBASE: FirebaseConfig
    GROQ: GroqConfig
    STORAGE: StorageConfig
    BACKBLAZE: BackblazeConfig
    PINECONE: PineconeConfig
    LLAMAPARSE: LlamaParseConfig
    COHERE_RERANKER: CohereRerankerConfig
    GEMINI: GeminiConfig
    SMTP: SMTPConfig
    # Optional: when present, email goes through the Gmail API over HTTPS
    # instead of SMTP (see app/utils/email.py).
    GMAIL_API: GmailApiConfig | None = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # .env also carries plain LangSmith tracing vars (LANGSMITH_API_KEY,
        # LANGCHAIN_*) alongside TRENCH_CONFIG -- those are read directly by
        # the langsmith/langchain SDKs via os.environ (see
        # app/utils/tracing.py), not through this Settings model, so they
        # must not fail validation here as unrecognized fields.
        extra="ignore",
    )

    TRENCH_CONFIG: TrenchConfig

    @property
    def is_dev(self) -> bool:
        return self.TRENCH_CONFIG.ENVIRONMENT.upper() == "DEV"

    @property
    def cors_origins(self) -> list[str]:
        cfg = self.TRENCH_CONFIG
        origins = cfg.CORS_ORIGINS or [cfg.FRONTEND_BASE_URL]
        # Browsers send the Origin header without a trailing slash.
        return [origin.rstrip("/") for origin in origins]


@lru_cache
def get_settings() -> Settings:
    return Settings()
