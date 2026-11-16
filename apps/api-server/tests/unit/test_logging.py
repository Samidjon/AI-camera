import json
import logging

from app.core.logging import JsonFormatter, redact, redact_mapping, request_id_ctx


def test_redacts_url_credentials():
    text = "connect rtsp://admin:SuperSecret1@10.0.0.5:554/stream failed"
    out = redact(text)
    assert "SuperSecret1" not in out
    assert "rtsp://admin:***@10.0.0.5" in out


def test_redacts_postgres_url():
    out = redact("postgresql+psycopg://postgres.abc:pa55w0rd@aws-0.pooler.supabase.com:5432/postgres")
    assert "pa55w0rd" not in out


def test_redacts_key_value_secrets():
    out = redact("password=hunter2 token: abc.def api_key=XYZ")
    assert "hunter2" not in out and "abc.def" not in out and "XYZ" not in out


def test_redact_mapping_masks_sensitive_keys():
    out = redact_mapping({"password": "p", "nested": {"token": "t"}, "user": "bob"})
    assert out == {"password": "***", "nested": {"token": "***"}, "user": "bob"}


def test_json_formatter_includes_request_id_and_redacts():
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "url=%s", ("rtsp://u:pw@h/",), None)
    record.password = "leak"
    tok = request_id_ctx.set("req-123")
    try:
        data = json.loads(JsonFormatter().format(record))
    finally:
        request_id_ctx.reset(tok)
    assert data["request_id"] == "req-123"
    assert "pw" not in data["msg"].split("@")[0].split(":")[-1]
    assert data["password"] == "***"
    assert data["level"] == "INFO"
