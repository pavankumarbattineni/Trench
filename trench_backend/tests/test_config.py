from app.config import DBConfig, Settings, get_settings


def test_settings_parse_trench_config_from_env():
    settings = get_settings()
    config = settings.TRENCH_CONFIG

    assert config.DB.database == "trench"
    assert config.DB.url.startswith("postgresql+asyncpg://")
    assert config.JWT.algorithm == "HS256"
    assert config.FIREBASE.credentials_json_base64


def _db(**overrides) -> DBConfig:
    fields = {
        "username": "trench",
        "password": "secret",
        "database": "trench",
        "ip_address": "db.example.com",
        "port": "5432",
    }
    return DBConfig(**{**fields, **overrides})


def test_db_urls_have_no_ssl_option_by_default():
    db = _db()

    assert db.url == "postgresql+asyncpg://trench:secret@db.example.com:5432/trench"
    assert db.conninfo == "postgresql://trench:secret@db.example.com:5432/trench"


def test_db_ssl_adds_each_drivers_own_option():
    """asyncpg (SQLAlchemy/Alembic) and psycopg (LangGraph checkpointer)
    spell "require TLS" differently -- each URL must use its own driver's."""
    db = _db(ssl=True)

    assert db.url.endswith("/trench?ssl=require")
    assert db.conninfo.endswith("/trench?sslmode=require")


def test_db_password_with_url_special_characters_is_encoded():
    db = _db(password="p@ss:w/rd%")

    assert "p%40ss%3Aw%2Frd%25@db.example.com" in db.url
    assert "p%40ss%3Aw%2Frd%25@db.example.com" in db.conninfo


def _settings(**trench_config_overrides) -> Settings:
    config = get_settings().TRENCH_CONFIG.model_copy(update=trench_config_overrides)
    return Settings.model_construct(TRENCH_CONFIG=config)


def test_cors_origins_default_to_the_frontend_url():
    settings = _settings(
        FRONTEND_BASE_URL="https://trench.vercel.app/", CORS_ORIGINS=None
    )

    assert settings.cors_origins == ["https://trench.vercel.app"]


def test_cors_origins_explicit_list_wins():
    settings = _settings(
        FRONTEND_BASE_URL="https://trench.vercel.app",
        CORS_ORIGINS=["https://trench.vercel.app", "https://app.example.com/"],
    )

    assert settings.cors_origins == [
        "https://trench.vercel.app",
        "https://app.example.com",
    ]
