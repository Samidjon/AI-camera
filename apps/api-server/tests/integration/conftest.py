"""Shared fixtures for integration tests: signed-in administrator / viewer, fresh rate limiter."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.ratelimit import limiter
from app.services.auth import create_user

PASSWORD = "a-strong-test-password"


@pytest.fixture(autouse=True)
def _fresh_limiter():
    limiter._buckets.clear()
    yield


@pytest.fixture
def admin(client: TestClient, db: Session) -> dict:
    create_user(db, email="admin@example.com", password=PASSWORD, role_name="administrator", full_name=None)
    db.commit()
    token = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": PASSWORD}).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


@pytest.fixture
def viewer(client: TestClient, db: Session) -> dict:
    create_user(db, email="viewer@example.com", password=PASSWORD, role_name="viewer", full_name=None)
    db.commit()
    token = client.post("/api/v1/auth/login", json={"email": "viewer@example.com", "password": PASSWORD}).json()
    return {"Authorization": f"Bearer {token['access_token']}"}
