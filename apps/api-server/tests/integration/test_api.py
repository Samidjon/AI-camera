from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.main import create_app
from app.models import AuditLog
from app.services.auth import create_user

PASSWORD = "a-strong-test-password"


def _make_user(db: Session, role: str = "viewer", email: str = "Viewer@Example.com", active: bool = True):
    user = create_user(db, email=email, password=PASSWORD, role_name=role, full_name="Test User")
    user.is_active = active
    db.commit()
    return user


def _login(client: TestClient, email: str, password: str = PASSWORD):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def test_health(client: TestClient):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.headers["X-Request-ID"]


def test_request_id_is_propagated(client: TestClient):
    r = client.get("/api/v1/health", headers={"X-Request-ID": "abc-123"})
    assert r.headers["X-Request-ID"] == "abc-123"


def test_system_status_with_db_and_without_redis(client: TestClient):
    body = client.get("/api/v1/system/status").json()
    assert body["status"] == "ok"
    assert body["components"]["database"]["state"] == "ok"
    assert body["components"]["database"]["detail"] == "sqlite"
    assert body["components"]["redis"]["state"] == "not_configured"
    assert body["environment"] == "test"


def test_system_status_reports_unconfigured_database():
    settings = Settings(_env_file=None, environment="test", database_url="", log_json=False)
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as c:
        # Liveness stays green even without a database (API process is healthy).
        assert c.get("/api/v1/health").status_code == 200
        body = c.get("/api/v1/system/status").json()
        assert body["status"] == "error"
        assert body["components"]["database"]["state"] == "not_configured"
        # Endpoints that need the DB fail with a clear 503, not a 500.
        r = c.post("/api/v1/auth/login", json={"email": "a@b.co", "password": "x"})
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "database_not_configured"


def test_system_version(client: TestClient):
    body = client.get("/api/v1/system/version").json()
    assert body["api_version"] == "v1"
    assert body["version"]
    assert body["python_version"].startswith("3.12")


def test_openapi_docs_available(client: TestClient):
    spec = client.get("/api/v1/openapi.json").json()
    paths = spec["paths"]
    for p in ("/api/v1/health", "/api/v1/system/status", "/api/v1/system/version", "/api/v1/auth/login"):
        assert p in paths
    assert client.get("/docs").status_code == 200


def test_unknown_route_uses_error_envelope(client: TestClient):
    r = client.get("/api/v1/does-not-exist")
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["code"] == "not_found"
    assert err["request_id"]


def test_validation_error_envelope(client: TestClient):
    r = client.post("/api/v1/auth/login", json={"email": "not-an-email"})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error"
    fields = {tuple(d["loc"])[-1] for d in err["details"]}
    assert {"email", "password"} <= fields


def test_login_and_me(client: TestClient, db: Session):
    _make_user(db)
    r = _login(client, "viewer@example.com")  # email is case-insensitive
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    assert r.json()["expires_in"] > 0

    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    data = me.json()
    assert data["email"] == "viewer@example.com"
    assert data["role"]["name"] == "viewer"
    assert "password_hash" not in data
    assert data["last_login_at"] is not None


def test_login_wrong_password_is_audited(client: TestClient, db: Session):
    _make_user(db)
    r = _login(client, "viewer@example.com", "wrong-password")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"
    db.expire_all()
    actions = db.scalars(select(AuditLog.action)).all()
    assert "auth.login_failed" in actions


def test_login_unknown_user_same_error(client: TestClient):
    r = _login(client, "nobody@example.com")
    assert r.status_code == 401
    assert r.json()["error"]["message"] == "Invalid email or password"


def test_disabled_user_cannot_login(client: TestClient, db: Session):
    _make_user(db, active=False)
    assert _login(client, "viewer@example.com").status_code == 401


def test_me_requires_token(client: TestClient):
    assert client.get("/api/v1/auth/me").status_code == 401
    r = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401
    assert r.headers.get("WWW-Authenticate") == "Bearer"


def test_cors_allows_dashboard_origin(client: TestClient):
    r = client.options(
        "/api/v1/health",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_intranet_email_domain_can_log_in(client: TestClient, db: Session):
    """Regression: strict validators reject '.local' — common in on-prem deployments."""
    _make_user(db, role="administrator", email="admin@site.local")
    r = _login(client, "  Admin@Site.LOCAL ")
    assert r.status_code == 200, r.text


def test_create_user_rejects_malformed_email(db: Session):
    import pytest

    from app.core.errors import AppError

    with pytest.raises(AppError):
        create_user(db, email="not-an-email", password=PASSWORD, role_name="viewer", full_name=None)


def test_access_token_carries_permissions_for_edge_agents(client: TestClient, db: Session, settings: Settings):
    from app.core.security import decode_access_token

    _make_user(db, role="operator", email="op@example.com")
    token = _login(client, "op@example.com").json()["access_token"]
    claims = decode_access_token(token, settings)
    assert claims["role"] == "operator"
    assert "cameras:control" in claims["perms"] and "cameras:manage" not in claims["perms"]


def test_cors_origin_regex_allows_vercel_deployments(settings, engine):
    """A dashboard on Vercel (production + preview URLs) is allowed by pattern, others are not."""
    from app.main import create_app

    s = settings.model_copy(update={"cors_origin_regex": r"https://camera-agent(-[a-z0-9-]+)?\.vercel\.app"})
    app = create_app(s)
    app.dependency_overrides[get_settings] = lambda: s
    with TestClient(app) as c:
        ok = c.get("/api/v1/health", headers={"Origin": "https://camera-agent-git-main-x.vercel.app"})
        assert ok.headers.get("access-control-allow-origin") == "https://camera-agent-git-main-x.vercel.app"
        bad = c.get("/api/v1/health", headers={"Origin": "https://evil.example.com"})
        assert "access-control-allow-origin" not in bad.headers
