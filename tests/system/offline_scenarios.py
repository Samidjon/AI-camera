"""MVP 09 offline / failure scenarios A–E against real processes (see stack.py).

    cd edge/camera-agent
    uv run python ../../tests/system/offline_scenarios.py [--outage-s 180]

A  AI off, no internet, central server off → capture, motion detection, local events continue.
B  AI on, central server off → local AI analysis continues, events stored locally.
C  AI switched off while running → the standard engine and the cameras keep running.
D  edge process killed hard (crash / power loss) and started again → recovers cameras, data, queue.
E  central server back after a long outage → accumulated events synchronised, nothing lost or doubled.

"No internet" is verified, not assumed: the agent's open sockets are checked for any non-loopback
peer. Results: tests/system/results/offline-<timestamp>.json (+ a printed summary).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stack import ROOT, Stack, remote_connections, wait_until  # noqa: E402

RESULTS = ROOT / "tests" / "system" / "results"
checks: list[dict] = []


def check(scenario: str, name: str, ok: bool, detail="") -> bool:
    checks.append({"scenario": scenario, "check": name, "ok": bool(ok), "detail": detail})
    print(f"  [{'PASS' if ok else 'FAIL'}] {scenario}: {name} {detail}")
    return ok


def cam_snapshot(s: Stack) -> dict:
    return {c["id"]: {"status": c["status"], "fps": c["fps"], "frames": c["frames_received"]} for c in s.cameras()}


def frames_advancing(s: Stack, seconds: float = 6) -> bool:
    a = cam_snapshot(s)
    time.sleep(seconds)
    b = cam_snapshot(s)
    return all(b[k]["frames"] > a[k]["frames"] and b[k]["status"] == "online" for k in a)


def analysed(s: Stack) -> dict:
    return {c["id"]: s.agent.get(f"/api/v1/cameras/{c['id']}/vision")["state"]["frames_analysed"] for c in s.cameras()}


def analysis_advancing(s: Stack, seconds: float = 6) -> tuple[bool, str]:
    """Standard engine works: every camera's analysed-frame counter grows."""
    a = analysed(s)
    time.sleep(seconds)
    b = analysed(s)
    return all(b[k] > a[k] for k in a), " ".join(f"{a[k]}→{b[k]}" for k in a)


def new_motion_within(s: Stack, timeout: float = 90) -> tuple[bool, str]:
    """Wait for the next Standard-engine event (motion episode or per-minute activity summary)."""
    start = motion_events(s)
    t0 = time.monotonic()
    try:
        wait_until(lambda: motion_events(s) > start, timeout, interval=1)
        return True, f"next standard-engine event after {time.monotonic() - t0:.0f}s ({start}→{motion_events(s)})"
    except TimeoutError:
        return False, f"no standard-engine event in {timeout:.0f}s"


def motion_events(s: Stack) -> int:
    db = s.agent_data / "events.db"
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as c:
        # every event the Standard Vision Engine produces (episodes + the per-minute activity summary:
        # with continuous traffic one motion episode can last minutes, the summary still comes each minute)
        return c.execute("SELECT COUNT(*) FROM events WHERE event_type IN ('motion_started','motion_ended',"
                         "'camera_motion_activity','object_entered_zone','object_exited_zone','line_crossed')"
                         ).fetchone()[0]


def server_event_count(s: Stack, device_id: str) -> tuple[int, int]:
    with sqlite3.connect(f"file:{s.server_db}?mode=ro", uri=True) as c:
        total = c.execute("SELECT COUNT(*) FROM camera_events WHERE device_id = ?", (device_id.replace("-", ""),)).fetchone()[0]
        if total == 0:  # UUID stored with dashes on some dialects
            total = c.execute("SELECT COUNT(*) FROM camera_events WHERE device_id = ?", (device_id,)).fetchone()[0]
        dup = c.execute("SELECT COUNT(*) - COUNT(DISTINCT id) FROM camera_events").fetchone()[0]
    return total, dup


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outage-s", type=float, default=180, help="central server outage length for scenario E")
    args = ap.parse_args()
    started = datetime.now(UTC)
    s = Stack(ROOT / "tests" / "system" / ".data" / "offline")
    timings: dict = {}
    try:
        # --- setup: server online, agent enrolled, two cameras (real video), then the server goes away
        s.start_server(fresh=True)
        s.start_agent(s.enrollment_token())
        cams = s.add_cameras(2)
        wait_until(lambda: all(c["status"] == "online" for c in s.cameras()), 60, what="cameras online")
        device_id = s.status()["device"]["device_id"]
        wait_until(lambda: s.status()["sync"]["state"] == "online", 60, what="link online (registered)")
        print("setup: agent registered, 2 cameras online")

        # ================================================================ A
        print("Scenario A — AI off, internet off, central server off")
        s.server.kill()
        check("A", "AI disabled", s.status()["ai"]["enabled"] is False)
        before = s.local_event_count()
        motion_before = motion_events(s)
        time.sleep(30)
        st = s.status()
        check("A", "capture continues (frames advancing, cameras online)", frames_advancing(s))
        check("A", "motion events recorded locally", motion_events(s) > motion_before,
              f"motion {motion_before}→{motion_events(s)}")  # fmt: skip
        check("A", "events queued for later upload", st["sync"]["queue"]["pending"] > 0,
              f"pending={st['sync']['queue']['pending']}")  # fmt: skip
        check("A", "agent reports server unreachable, not crashed", st["sync"]["state"] != "online",
              f"state={st['sync']['state']} reason={st['sync']['reason']}")  # fmt: skip
        remote = remote_connections(s.agent_proc.pid)
        check("A", "no connection outside loopback (works without internet)", not remote, str(remote))
        check("A", "local database integrity ok", st["database_ok"] is True)
        timings["A_local_events_added"] = s.local_event_count() - before

        # ================================================================ B
        print("Scenario B — AI on, central server off")
        ai = s.agent.post("/api/v1/ai/enable")
        ok = wait_until(lambda: s.agent.get("/api/v1/ai/status")["mode"] == "active", 90, what="AI active")
        check("B", "AI engine active offline (local model)", bool(ok), ai.get("model", {}).get("id", "") if isinstance(ai.get("model"), dict) else "")
        m0 = s.agent.get("/api/v1/ai/status").get("metrics", {})
        time.sleep(30)
        aist = s.agent.get("/api/v1/ai/status")
        m1 = aist.get("metrics", {})
        check("B", "inference keeps running", (m1.get("inferences") or 0) > (m0.get("inferences") or 0),
              f"inferences {m0.get('inferences')}→{m1.get('inferences')}, p95 {m1.get('latency_p95_ms')} ms")  # fmt: skip
        check("B", "cameras keep streaming with AI", frames_advancing(s))
        ok, detail = analysis_advancing(s)
        check("B", "standard engine keeps analysing alongside AI", ok, f"frames analysed {detail}")
        ok, detail = new_motion_within(s)
        check("B", "events still stored locally", ok, detail)
        timings["B_ai_metrics"] = m1

        # ================================================================ C
        print("Scenario C — AI switched off while running")
        frames_before = cam_snapshot(s)
        s.agent.post("/api/v1/ai/disable")
        check("C", "AI mode becomes disabled", wait_until(lambda: s.agent.get("/api/v1/ai/status")["mode"] == "disabled", 30))
        after = cam_snapshot(s)
        check("C", "cameras were not restarted (frame counters keep growing)",
              all(after[k]["frames"] >= frames_before[k]["frames"] for k in after))  # fmt: skip
        ok, detail = analysis_advancing(s)
        check("C", "standard engine keeps analysing", ok, f"frames analysed {detail}")
        ok, detail = new_motion_within(s)
        check("C", "standard engine keeps detecting motion", ok, detail)
        check("C", "cameras online", frames_advancing(s))

        # ================================================================ D
        print("Scenario D — edge process killed hard (crash / power loss), started again")
        local_before = s.local_event_count()
        pending_before = s.status()["sync"]["queue"]["pending"]
        t0 = time.monotonic()
        s.agent_proc.kill()
        s.start_agent()  # same data dir, no enrollment token: must reuse its stored credentials
        t_up = time.monotonic() - t0
        wait_until(lambda: len(s.cameras()) == 2 and all(c["status"] == "online" for c in s.cameras()), 60,
                   what="cameras back online")  # fmt: skip
        t_cams = time.monotonic() - t0
        st = s.status()
        check("D", "agent back up", True, f"API {t_up:.1f}s, cameras online {t_cams:.1f}s after the crash")
        check("D", "cameras restored automatically", {c["id"] for c in s.cameras()} == set(cams))
        check("D", "no local events lost", s.local_event_count() >= local_before,
              f"{local_before}→{s.local_event_count()}")  # fmt: skip
        check("D", "upload queue preserved", st["sync"]["queue"]["pending"] >= pending_before * 0.99,
              f"pending {pending_before}→{st['sync']['queue']['pending']}")  # fmt: skip
        check("D", "databases intact after hard kill", st["database_ok"] is True)
        check("D", "same device identity", st["device"]["device_id"] == device_id)
        check("D", "AI setting persisted (still off)", st["ai"]["enabled"] is False)
        timings["D_restart_s"] = {"api": round(t_up, 1), "cameras_online": round(t_cams, 1)}

        # ================================================================ E
        print(f"Scenario E — central server back after a long outage ({args.outage_s:.0f} s more)")
        time.sleep(args.outage_s)
        pending = s.status()["sync"]["queue"]["pending"]
        outage_total = (datetime.now(UTC) - started).total_seconds()
        t0 = time.monotonic()
        s.start_server()
        wait_until(lambda: s.status()["sync"]["queue"]["pending"] == 0, 600, interval=2, what="queue drained")
        t_sync = time.monotonic() - t0
        time.sleep(3)
        local_total = s.local_event_count()
        server_total, dup = server_event_count(s, device_id)
        st = s.status()
        check("E", "accumulated queue uploaded", True, f"{pending} events in {t_sync:.1f}s after the server came back")
        check("E", "server has every local event", server_total >= local_total - st["sync"]["queue"]["pending"],
              f"local={local_total} server={server_total}")  # fmt: skip
        check("E", "no duplicates on the server", dup == 0, f"duplicates={dup}")
        check("E", "nothing dropped from the queue", st["sync"]["queue"]["dropped"] == 0)
        check("E", "device online on the server again",
              wait_until(lambda: s.api.get(f"/api/v1/devices/{device_id}")["status"] in ("online", "degraded"), 60))  # fmt: skip
        timings["E"] = {"outage_s": round(outage_total), "queued": pending, "drain_s": round(t_sync, 1)}
    finally:
        s.stop_all()

    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"offline-{started.strftime('%Y%m%dT%H%M%SZ')}.json"
    failed = [c for c in checks if not c["ok"]]
    out.write_text(json.dumps({"started": started.isoformat(), "checks": checks, "measurements": timings,
                               "passed": not failed}, indent=2), encoding="utf-8")  # fmt: skip
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed → {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
