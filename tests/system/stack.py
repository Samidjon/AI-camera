"""Real-process test stack for the MVP 09 system tests (offline scenarios, performance).

Starts the REAL central server (FastAPI, fresh SQLite) and the REAL edge agent (file-source cameras
looping a real video) as separate OS processes on their own ports and data directories, so a
developer's running instances are untouched. Processes can be killed hard (crash / power loss),
stopped gracefully and restarted on the same data, exactly like in the field.

Optionally a counting TCP proxy sits between agent and server to measure upload bandwidth.
Only loopback addresses are used.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_DIR = ROOT / "apps" / "api-server"
AGENT_DIR = ROOT / "edge" / "camera-agent"
SECRET = "system-test-jwt-secret-0123456789abcdef012345"
ADMIN = ("admin@system.test", "system-test-password-123")
SAMPLE = AGENT_DIR / "data" / "samples" / "demo-parking.mp4"
MODELS = AGENT_DIR / "models"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Http:
    def __init__(self, base: str, token: str | None = None) -> None:
        self.base = base.rstrip("/")
        self.token = token

    def call(self, method: str, path: str, body=None, timeout: float = 15):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw and r.headers.get_content_type() == "application/json" else raw

    def get(self, path: str, **kw):
        return self.call("GET", path, **kw)

    def post(self, path: str, body=None, **kw):
        return self.call("POST", path, body, **kw)

    def alive(self) -> bool:
        try:
            self.get("/api/v1/health", timeout=2)
            return True
        except (urllib.error.URLError, OSError, ConnectionError):
            return False


def wait_until(fn, timeout: float, interval: float = 0.5, what: str = "condition"):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        try:
            last = fn()
            if last:
                return last
        except Exception as exc:  # noqa: BLE001 - retried until timeout
            last = exc
        time.sleep(interval)
    raise TimeoutError(f"timed out waiting for {what} (last: {last!r})")


class Proc:
    """A service process (uv run …) with its log file; kill() = hard crash, stop() = graceful."""

    def __init__(self, name: str, args: list[str], cwd: Path, env: dict, log: Path) -> None:
        self.name, self.args, self.cwd, self.env, self.log_path = name, args, cwd, env, log
        self.p: subprocess.Popen | None = None

    def start(self) -> None:
        self.log = open(self.log_path, "ab")  # noqa: SIM115 - kept open for the process lifetime
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.p = subprocess.Popen(self.args, cwd=self.cwd, env=self.env, stdout=self.log, stderr=subprocess.STDOUT,
                                  creationflags=flags, start_new_session=os.name != "nt")  # fmt: skip

    @property
    def pid(self) -> int | None:
        return self.p.pid if self.p else None

    def running(self) -> bool:
        return self.p is not None and self.p.poll() is None

    def _tree(self):
        import psutil

        try:
            root = psutil.Process(self.p.pid)
            return [root, *root.children(recursive=True)]
        except psutil.NoSuchProcess:
            return []

    def kill(self) -> None:
        """Hard kill of the whole process tree (no cleanup handlers run): crash / power loss."""
        if not self.running():
            return
        for proc in reversed(self._tree()):
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        self.p.wait(15)
        self.log.close()

    def stop(self, timeout: float = 30) -> None:
        """Graceful stop (SIGTERM / CTRL_BREAK on Windows), then hard kill after the timeout."""
        if not self.running():
            return
        try:
            if os.name == "nt":
                self.p.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(self.p.pid, signal.SIGTERM)
            self.p.wait(timeout)
        except Exception:  # noqa: BLE001
            self.kill()
            return
        for proc in self._tree():  # uv may leave the python child behind on Windows
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        self.log.close()


class CountingProxy:
    """Loopback TCP proxy counting bytes agent → server (upload) and back."""

    def __init__(self, target_port: int) -> None:
        self.target = target_port
        self.port = free_port()
        self.up = 0
        self.down = 0
        self._lock = threading.Lock()
        self._srv = socket.create_server(("127.0.0.1", self.port))
        threading.Thread(target=self._accept, daemon=True).start()

    def _pipe(self, src, dst, upstream: bool):
        try:
            while data := src.recv(65536):
                dst.sendall(data)
                with self._lock:
                    if upstream:
                        self.up += len(data)
                    else:
                        self.down += len(data)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.close()
                except OSError:
                    pass

    def _accept(self):
        while True:
            try:
                client, _ = self._srv.accept()
            except OSError:
                return
            try:
                server = socket.create_connection(("127.0.0.1", self.target), timeout=5)
            except OSError:
                client.close()  # server down → the agent sees a refused/closed connection
                continue
            threading.Thread(target=self._pipe, args=(client, server, True), daemon=True).start()
            threading.Thread(target=self._pipe, args=(server, client, False), daemon=True).start()

    def counters(self) -> tuple[int, int]:
        with self._lock:
            return self.up, self.down


class Stack:
    def __init__(self, workdir: Path, *, with_server: bool = True, proxy: bool = False) -> None:
        self.dir = workdir
        shutil.rmtree(workdir, ignore_errors=True)
        workdir.mkdir(parents=True)
        self.server_port = free_port()
        self.agent_port = free_port()
        self.proxy = CountingProxy(self.server_port) if proxy else None
        self.server_db = workdir / "central.db"
        self.agent_data = workdir / "agent"
        self.agent_data.mkdir()
        self.server_url = f"http://127.0.0.1:{self.server_port}"
        upload_port = self.proxy.port if self.proxy else self.server_port
        self.agent_server_url = f"http://127.0.0.1:{upload_port}" if with_server else ""
        self.api = Http(self.server_url)
        self.agent = Http(f"http://127.0.0.1:{self.agent_port}")
        uv = shutil.which("uv") or "uv"
        base_env = {k: v for k, v in os.environ.items() if not k.startswith(("ACI_", "VIRTUAL_ENV"))}
        self.server = Proc(
            "server",
            [uv, "run", "--project", str(API_DIR), "python", str(Path(__file__).with_name("run_server.py"))],
            API_DIR,
            base_env | {
                "ACI_DATABASE_URL": f"sqlite:///{self.server_db.as_posix()}", "ACI_ENVIRONMENT": "development",
                "ACI_JWT_SECRET": SECRET, "ACI_LOG_JSON": "false", "ACI_LOG_LEVEL": "WARNING",
                "ACI_RATE_HEARTBEAT_BURST": "1000", "ACI_RATE_EVENTS_BURST": "1000", "ACI_REDIS_URL": "",
                "ACI_RATE_LOGIN_BURST": "1000", "ACI_RATE_LOGIN_ACCOUNT_BURST": "1000",
                "SYSTEM_TEST_PORT": str(self.server_port), "SYSTEM_TEST_ADMIN": json.dumps(ADMIN),
            },
            workdir / "server.log",
        )  # fmt: skip
        self.agent_env = base_env | {
            "ACI_AGENT_ENVIRONMENT": "development", "ACI_AGENT_DATA_DIR": str(self.agent_data),
            "ACI_AGENT_PORT": str(self.agent_port), "ACI_AGENT_JWT_SECRET": SECRET,
            "ACI_AGENT_LOG_JSON": "false", "ACI_AGENT_LOG_LEVEL": "WARNING",
            "ACI_AGENT_SERVER_URL": self.agent_server_url, "ACI_AGENT_SERVER_ALLOW_INSECURE_HTTP": "true",
            "ACI_AGENT_SERVER_HEARTBEAT_INTERVAL_S": "5", "ACI_AGENT_SERVER_BACKOFF_MAX_S": "20",
            "ACI_AGENT_RUNTIME_BACKUP_INTERVAL_H": "0", "ACI_AGENT_AI_MODELS_DIR": str(MODELS),
            "ACI_AGENT_MAX_CAMERAS": "64",
        }  # fmt: skip
        self.agent_proc = Proc(
            "agent",
            [uv, "run", "--project", str(AGENT_DIR), "python", "-m", "aci_agent", "run"],
            AGENT_DIR,
            self.agent_env,
            workdir / "agent.log",
        )
        self.token: str | None = None

    # ------------------------------------------------------------------ server

    def start_server(self, fresh: bool = False, timeout: float = 120) -> None:
        self.server.env = self.server.env | {"SYSTEM_TEST_FRESH": "1" if fresh else "0"}
        self.server.start()
        wait_until(self.api.alive, timeout, what="central server")
        self.token = self.api.post("/api/v1/auth/login", {"email": ADMIN[0], "password": ADMIN[1]})["access_token"]
        self.api.token = self.token
        self.agent.token = self.token  # agent and server share the JWT secret in this test stack

    def enrollment_token(self) -> str:
        return self.api.post("/api/v1/devices/enrollment-tokens", {"name": "system-test"})["token"]

    # ------------------------------------------------------------------ agent

    def start_agent(self, enrollment: str | None = None, timeout: float = 120) -> None:
        if enrollment:
            self.agent_proc.env = self.agent_env | {"ACI_AGENT_SERVER_ENROLLMENT_TOKEN": enrollment}
        self.agent_proc.start()
        wait_until(self.agent.alive, timeout, what="edge agent")

    def add_cameras(self, n: int, video: Path = SAMPLE, prefix: str = "Cam") -> list[str]:
        ids = []
        for i in range(n):
            r = self.agent.post("/api/v1/cameras", {"name": f"{prefix} {i + 1}", "source_type": "file",
                                                     "source_uri": str(video), "options": {"loop": True},
                                                     "connect": True})  # fmt: skip
            ids.append(r["id"])
        return ids

    def status(self) -> dict:
        return self.agent.get("/api/v1/system/status")

    def cameras(self) -> list[dict]:
        return self.agent.get("/api/v1/cameras")

    def local_event_count(self) -> int:
        return int(self.status()["events"]["events_total"])

    def stop_all(self) -> None:
        for p in (self.agent_proc, self.server):
            try:
                p.stop(20)
            except Exception:  # noqa: BLE001
                p.kill()


def remote_connections(pid: int) -> list[str]:
    """Non-loopback TCP/UDP endpoints of a process tree (proof that nothing goes to the internet)."""
    import psutil

    try:
        root = psutil.Process(pid)
        procs = [root, *root.children(recursive=True)]
    except psutil.NoSuchProcess:
        return []
    out = []
    for p in procs:
        try:
            for c in p.net_connections(kind="inet"):
                if c.raddr and not (c.raddr.ip.startswith("127.") or c.raddr.ip in ("::1", "0.0.0.0")):
                    out.append(f"{c.raddr.ip}:{c.raddr.port}")
        except Exception:  # noqa: BLE001
            continue
    return out


if __name__ == "__main__":  # smoke test
    s = Stack(ROOT / "tests" / "system" / ".data" / "smoke")
    try:
        s.start_server(fresh=True)
        s.start_agent(s.enrollment_token())
        s.add_cameras(1)
        print(wait_until(lambda: s.cameras()[0]["status"] == "online" and s.cameras()[0], 60, what="camera online"))
        print(json.dumps(s.status(), indent=1)[:1500])
    finally:
        s.stop_all()
    sys.exit(0)
