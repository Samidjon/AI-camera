"""Edge agent CLI: status / backup / env file writing (no running agent needed)."""

from __future__ import annotations

import json
import os
import stat
import sys

import pytest

from aci_agent.__main__ import main, write_env_file


@pytest.fixture
def data_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no stray .env
    monkeypatch.setenv("ACI_AGENT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ACI_AGENT_PORT", "18999")
    return tmp_path


def test_status_and_backup_commands(data_env, capsys):
    assert main(["status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert len(status["device_id"]) == 36 and status["database_ok"] and status["agent_running"] is False
    assert main(["backup"]) == 0
    backup = json.loads(capsys.readouterr().out)
    assert set(backup["files"]) == {"agent", "events"}
    assert main(["status"]) == 0
    again = json.loads(capsys.readouterr().out)
    assert again["device_id"] == status["device_id"] and again["latest_backup"] == backup["id"]


def test_restore_refuses_unknown_backup(data_env, capsys):
    assert main(["restore", "../../etc"]) == 2


def test_env_file_is_owner_only(tmp_path):
    path = tmp_path / "etc" / "agent.env"
    write_env_file(path, {"ACI_AGENT_JWT_SECRET": "x" * 32, "ACI_AGENT_SERVER_URL": "", "ACI_AGENT_PORT": "8100"})
    text = path.read_text(encoding="utf-8")
    assert "ACI_AGENT_JWT_SECRET=" in text and "ACI_AGENT_SERVER_URL" not in text  # empty values omitted
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
