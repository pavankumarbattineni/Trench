from app.core.config import get_settings


def test_settings_load_database_url_from_env():
    settings = get_settings()
    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.app_name == "Trench"
