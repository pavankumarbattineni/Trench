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


class TrenchConfig(BaseModel):
    ENVIRONMENT: str = "DEV"
    DB: DBConfig
    JWT: JWTConfig
    MCP: MCPConfig
    FIREBASE: FirebaseConfig
    GROQ: GroqConfig
    STORAGE: StorageConfig


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    TRENCH_CONFIG: TrenchConfig

    @property
    def is_dev(self) -> bool:
        return self.TRENCH_CONFIG.ENVIRONMENT.upper() == "DEV"


@lru_cache
def get_settings() -> Settings:
    return Settings()
