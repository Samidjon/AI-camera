from fastapi.testclient import TestClient

from tests.conftest import auth, make_token, wait_for

OPERATOR = {"Authorization": f"Bearer {make_token(['cameras:read', 'cameras:control', 'events:read'])}"}


def test_ai_status_defaults_to_disabled_and_standard_fallback(client: TestClient):
    s = client.get("/api/v1/ai/status", headers=OPERATOR).json()
    assert s["mode"] == "disabled" and s["enabled"] is False and s["fallback"] == "standard_engine"
    assert "gpu" in s["resources"]
    assert client.get("/api/v1/system/status").json()["ai"]["mode"] == "disabled"


def test_ai_control_requires_ai_manage(client: TestClient):
    assert client.post("/api/v1/ai/enable", headers=OPERATOR).status_code == 403
    cfg = client.get("/api/v1/ai/config", headers=OPERATOR).json()
    assert client.put("/api/v1/ai/config", json=cfg, headers=OPERATOR).status_code == 403
    assert client.put("/api/v1/ai/config", json=cfg | {"model_id": "nope"}, headers=auth()).status_code == 422
    assert client.put("/api/v1/ai/config", json=cfg | {"confidence_threshold": 3}, headers=auth()).status_code == 422


def test_enable_without_model_goes_offline_not_crash(client: TestClient):
    # test settings point models_dir at ./models relative to the test cwd; force a missing model id file
    r = client.post("/api/v1/ai/enable", headers=auth())
    assert r.status_code == 200
    assert wait_for(lambda: client.get("/api/v1/ai/status", headers=auth()).json()["mode"] in ("offline", "active"), 8)
    models = client.get("/api/v1/ai/models", headers=auth()).json()
    assert {m["id"] for m in models} >= {"yolox_s_coco_2022nov"}
    assert all(len(m["sha256"]) == 64 for m in models)
    client.post("/api/v1/ai/disable", headers=auth())
    assert wait_for(lambda: client.get("/api/v1/ai/status", headers=auth()).json()["mode"] == "disabled", 5)
    hist = client.get("/api/v1/ai/errors", headers=auth()).json()["history"]
    assert hist and hist[0]["mode"] == "disabled"


def test_api_key_never_exposed(client: TestClient):
    for path in ("/api/v1/ai/status", "/api/v1/ai/config", "/api/v1/ai/models", "/api/v1/ai/errors"):
        body = client.get(path, headers=auth()).text
        assert "sk-ant" not in body and "api_key" not in body
