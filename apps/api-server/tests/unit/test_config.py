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


def test_orm_specific_params_are_removed():
    """Supabase's Prisma-style URL (transaction pooler) carries pgbouncer=true, which psycopg rejects."""
    s = Settings(
        _env_file=None,
        database_url="postgresql://postgres.ref:p%40ss@aws-1-x.pooler.supabase.com:6543/postgres"
        "?pgbouncer=true&connection_limit=1&sslmode=require",
    )
    assert s.database_url == (
        "postgresql+psycopg://postgres.ref:p%40ss@aws-1-x.pooler.supabase.com:6543/postgres?sslmode=require"
    )
    only_orm = Settings(_env_file=None, database_url="postgresql://u:p@h:6543/db?pgbouncer=true")
    assert only_orm.database_url == "postgresql+psycopg://u:p@h:6543/db"


def test_raw_password_with_reserved_characters_is_encoded():
    s = Settings(_env_file=None, database_url="postgresql://postgres.ref:a/b?c[d]@e@host:6543/postgres?pgbouncer=true")
    assert s.database_url == "postgresql+psycopg://postgres.ref:a%2Fb%3Fc%5Bd%5D%40e@host:6543/postgres"


def test_already_encoded_password_is_not_double_encoded():
    s = Settings(_env_file=None, database_url="postgresql://u:a%2Fb@host:5432/db?sslmode=require")
    assert s.database_url == "postgresql+psycopg://u:a%2Fb@host:5432/db?sslmode=require"


def test_sqlite_url_untouched():
    assert Settings(_env_file=None, database_url="sqlite:///./dev.db").database_url == "sqlite:///./dev.db"
