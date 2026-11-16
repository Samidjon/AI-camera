from app.core.config import Settings
from app.schemas.system import ComponentState
from app.services.health import check_database, check_redis


def test_database_not_configured():
    s = Settings(_env_file=None, database_url="")
    assert check_database(s).state == ComponentState.NOT_CONFIGURED


def test_redis_not_configured_is_not_an_error():
    s = Settings(_env_file=None, redis_url="")
    assert check_redis(s).state == ComponentState.NOT_CONFIGURED


def test_redis_unreachable_reports_error_without_raising():
    # Port 1 on localhost is closed; the check must fail fast and never raise.
    s = Settings(_env_file=None, redis_url="redis://:secretpw@127.0.0.1:1/0")
    result = check_redis(s)
    assert result.state == ComponentState.ERROR
    assert "secretpw" not in (result.detail or "")
