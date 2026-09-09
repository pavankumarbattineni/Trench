from app.config import get_settings


def test_settings_parse_trench_config_from_env():
    settings = get_settings()
    config = settings.TRENCH_CONFIG

    assert config.DB.database == "trench"
    assert config.DB.url.startswith("postgresql+asyncpg://")
    assert config.JWT.algorithm == "HS256"
    assert config.FIREBASE.credentials_path.endswith(".json")
