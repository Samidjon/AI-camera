"""Auth foundation: the permission guard used by future endpoints."""

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.config import Settings, get_settings
from app.main import create_app
from app.services.auth import create_user

PASSWORD = "a-strong-test-password"


def _app_with_guarded_route(settings: Settings) -> FastAPI:
    app = create_app(settings)
    app.dependency_overrides[get_settings] = lambda: settings

    @app.get("/guarded", dependencies=[Depends(require_permission("cameras:control"))])
    def guarded():
        return {"ok": True}

    return app


def _token(c: TestClient, email: str) -> str:
    return c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).json()["access_token"]


def test_permission_guard(settings: Settings, engine, db: Session):
    for role in ("administrator", "operator", "viewer"):
        create_user(db, email=f"{role}@example.com", password=PASSWORD, role_name=role, full_name=None)
    db.commit()

    with TestClient(_app_with_guarded_route(settings)) as c:
        results = {
            role: c.get("/guarded", headers={"Authorization": f"Bearer {_token(c, f'{role}@example.com')}"}).status_code
            for role in ("administrator", "operator", "viewer")
        }
    assert results == {"administrator": 200, "operator": 200, "viewer": 403}
