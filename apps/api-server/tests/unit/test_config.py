import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_supabase_url_is_normalized_to_psycopg_driver():
    s = Settings(_env_file=None, database_url="postgresql://u:p@db.example.supabase.co:5432/postgres")
    assert s.database_url.startswith("postgresql+psycopg://")
    s2 = Settings(_env_file=None, database_url="postgres://u:p@h/db")
    assert s2.database_url == "postgresql+psycopg://u:p@h/db"


def test_empty_database_means_not_configured():
    s = Settings(_env_file=None, database_url="")
    assert s.database_configured is False
    assert s.redis_configured is False


def test_production_rejects_default_secret():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="production", database_url="sqlite://")


def test_production_requires_database():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="production", jwt_secret="x" * 40, database_url="")


def test_production_accepts_valid_config():
    s = Settings(_env_file=None, environment="production", jwt_secret="x" * 40, database_url="sqlite://")
    assert s.environment == "production"
