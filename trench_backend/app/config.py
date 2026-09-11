from functools import lru_cache

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

    @property
    def url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.username}:{self.password}"
            f"@{self.ip_address}:{self.port}/{self.database}"
        )


class JWTConfig(BaseModel):
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30


class MCPConfig(BaseModel):
    encryption_key: str


class FirebaseConfig(BaseModel):
    credentials_path: str


class GroqConfig(BaseModel):
    api_key: str


class StorageConfig(BaseModel):
    local_root_path: str


class PineconeConfig(BaseModel):
    api_key: str
    # Personal knowledge lives in namespace f"personal:{user_id}", company
    # knowledge in f"company:{organization_id}" -- both inside this one
    # operator-owned index (BYOK users get their own index/project instead,
    # resolved through UserCredential rather than this config).
    index_name: str = "trench-platform"


class LlamaParseConfig(BaseModel):
    api_key: str


class GeminiConfig(BaseModel):
    api_key: str


class TrenchConfig(BaseModel):
    ENVIRONMENT: str = "DEV"
    DB: DBConfig
    JWT: JWTConfig
    MCP: MCPConfig
    FIREBASE: FirebaseConfig
    GROQ: GroqConfig
    STORAGE: StorageConfig
    PINECONE: PineconeConfig
    LLAMAPARSE: LlamaParseConfig
    GEMINI: GeminiConfig


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


@lru_cache
def get_settings() -> Settings:
    return Settings()
