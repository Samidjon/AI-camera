"""Camera / AI health transitions become local system events (MVP 07)."""

from __future__ import annotations

from types import SimpleNamespace

from aci_agent.agent.health_events import HealthEventMonitor


def view(cid: str, status: str, error: str | None = None):
    return SimpleNamespace(
        config=SimpleNamespace(id=cid, name=f"Cam {cid}"),
        runtime=SimpleNamespace(status=status, failure_kind="unreachable" if error else None, last_error=error),
    )


def test_camera_and_ai_transitions():
    cams = [view("a", "connecting")]
    ai = [(False, "disabled", None)]
    emitted = []
    mon = HealthEventMonitor(lambda: cams, lambda: ai[0], emitted.append)
    mon.check()
    cams[0] = view("a", "error", "timeout")  # never online yet: no error storm at start-up
    mon.check()
    assert emitted == []
    cams[0] = view("a", "online")
    mon.check()
    cams[0] = view("a", "reconnecting", "stream lost")
    mon.check()
    mon.check()  # still failing: reported once
    cams[0] = view("a", "online")
    mon.check()
    assert [e.event_type for e in emitted] == ["camera_error", "camera_recovered"]
    assert emitted[0].metadata["failure_kind"] == "unreachable" and emitted[0].source == "system"
    ai[0] = (True, "error", "model missing")
    mon.check()
    mon.check()
    ai[0] = (True, "active", None)
    mon.check()
    assert [e.event_type for e in emitted[2:]] == ["ai_error", "ai_recovered"]
    assert emitted[2].camera_id == "" and emitted[2].metadata["reason"] == "model missing"
    fp1 = mon.fingerprint()
    cams[0] = view("a", "offline")
    assert mon.fingerprint() != fp1
