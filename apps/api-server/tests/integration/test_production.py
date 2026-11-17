"""MVP 09 production hardening: monitoring snapshot, security headers, input validation / injection,
authentication edge cases."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.integration.test_devices import Agent, enroll, event

API = "/api/v1"


def test_monitoring_snapshot(client: TestClient, admin: dict, viewer: dict):
    a = Agent(client, "Gate box")
    a.register(enroll(client, admin))
    a.heartbeat(sync={"pending": 12, "dropped": 0}, ai={"enabled": True, "mode": "degraded", "reason": "model missing"},
                camera_list=[{"id": "cam-a", "name": "Gate", "source_type": "rtsp", "status": "error", "fps": None}],
                cameras={"total": 1, "by_status": {"error": 1}})  # fmt: skip
    r = client.get(f"{API}/system/monitoring", headers=admin)
    assert r.status_code == 200, r.text
    m = r.json()
    comps = m["components"]
    assert set(comps) == {"backend", "database", "redis", "disk", "edge_devices", "cameras", "ai", "synchronization"}
    assert comps["database"]["state"] == "ok"
    assert comps["redis"]["state"] == "not_configured"
    assert comps["edge_devices"]["total"] == 1 and comps["edge_devices"]["devices"][0]["stale"] is False
    assert comps["ai"]["problems"] == [{"device": "Gate box", "mode": "degraded", "reason": "model missing"}]
    assert comps["synchronization"]["devices_with_backlog"][0]["pending"] == 12
    assert comps["cameras"]["problems"][0]["status"] == "error"
    assert m["status"] == "warning"  # problems present, nothing down
    assert client.get(f"{API}/system/monitoring", headers=viewer).status_code == 200  # devices:read
    assert client.get(f"{API}/system/monitoring").status_code == 401


def test_security_headers(client: TestClient):
    r = client.get(f"{API}/health")
    h = r.headers
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert h["Referrer-Policy"] == "no-referrer"
    assert h["Cache-Control"] == "no-store"
    assert "default-src 'none'" in h["Content-Security-Policy"]
    assert "Strict-Transport-Security" not in h  # plain HTTP
    https = client.get(f"{API}/health", headers={"X-Forwarded-Proto": "https"})
    assert https.headers["Strict-Transport-Security"].startswith("max-age=")
    # bearer tokens only: CORS never allows credentials (no cookies → no CSRF)
    pre = client.options(
        f"{API}/health", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"}
    )
    assert pre.headers.get("access-control-allow-credentials") is None


def test_injection_and_validation(client: TestClient, admin: dict):
    a = Agent(client, "edge")
    a.register(enroll(client, admin))
    a.upload([event("cam-a", "motion_started")])
    # SQL metacharacters in search / filters are data, not SQL
    for q in ["' OR 1=1 --", '"; DROP TABLE camera_events; --', "%' UNION SELECT * FROM users --"]:
        r = client.get(f"{API}/events", params={"q": q}, headers=admin)
        assert r.status_code == 200 and r.json()["items"] == []
    assert client.get(f"{API}/events", headers=admin).json()["items"]  # table intact
    # script in a name is stored as text and returned verbatim (escaped by the dashboard), never executed
    r = client.post(f"{API}/alert-rules", json={"name": "<script>alert(1)</script>", "kind": "motion_detected"},
                    headers=admin)  # fmt: skip
    assert r.status_code == 201 and r.json()["name"] == "<script>alert(1)</script>"
    assert r.headers["content-type"].startswith("application/json")
    # oversized / malformed input is rejected with 422, not 500
    assert client.post(f"{API}/alert-rules", json={"name": "x" * 1000, "kind": "motion_detected"},
                       headers=admin).status_code == 422  # fmt: skip
    assert client.get(f"{API}/analytics/summary", params={"tz": "../../etc/passwd"}, headers=admin).status_code == 400
    assert client.get(f"{API}/alerts/not-a-uuid/deliveries", headers=admin).status_code == 422


def test_auth_edge_cases(client: TestClient, admin: dict):
    token = admin["Authorization"].split()[1]
    head, body, sig = token.split(".")
    for bad in [f"{head}.{body}.AAAA", f"{head}.{body}", "Bearer", "x.y.z", token[:-2]]:
        r = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {bad}"})
        assert r.status_code == 401, bad
    # alg=none forgery
    import base64
    import json

    none_head = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    r = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {none_head}.{body}."})
    assert r.status_code == 401
    # device secret is not a user token and vice versa
    assert client.get(f"{API}/devices", headers={"Authorization": "Bearer not-a-device-secret"}).status_code == 401


def test_login_brute_force_is_throttled(client: TestClient, admin: dict, settings):
    settings.rate_login_burst = 3
    settings.rate_login_per_min = 0.01
    codes = [
        client.post(f"{API}/auth/login", json={"email": "admin@example.com", "password": f"wrong-{i}"}).status_code
        for i in range(5)
    ]
    assert codes[:2] == [401, 401] and codes[-1] == 429  # admin fixture already used one token
    r = client.post(f"{API}/auth/login", json={"email": "admin@example.com", "password": "x"})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1


def test_login_throttled_per_account_across_ips(client: TestClient, admin: dict, settings):
    settings.rate_login_account_burst = 2
    settings.rate_login_account_per_hour = 0.01
    codes = [
        client.post(
            f"{API}/auth/login",
            json={"email": "victim@example.com", "password": "guess"},
            headers={"X-Forwarded-For": f"203.0.113.{i}"},
        ).status_code  # fmt: skip
        for i in range(4)
    ]
    assert codes[-1] == 429
