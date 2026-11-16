"""RTSP diagnostics against a real local TCP server speaking RTSP."""

import base64
import hashlib
import re
import socket
import socketserver
import threading
from collections.abc import Iterator

import pytest

from aci_agent.camera.models import FailureKind
from aci_agent.camera.rtsp_probe import probe_rtsp

USER, PASSWORD, REALM, NONCE = "admin", "c0rrect", "IPCam", "abc123"


class _Handler(socketserver.BaseRequestHandler):
    mode = "digest"  # digest | basic | open | notfound | garbage

    def handle(self):
        buf = b""
        self.request.settimeout(2)
        while True:
            try:
                chunk = self.request.recv(4096)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while b"\r\n\r\n" in buf:
                req, buf = buf.split(b"\r\n\r\n", 1)
                self.request.sendall(self.respond(req.decode()))

    def respond(self, req: str) -> bytes:
        mode = type(self).mode
        cseq = re.search(r"CSeq: (\d+)", req).group(1)
        if mode == "garbage":
            return b"HTTP/1.1 200 OK\r\n\r\n"
        if mode == "notfound":
            return f"RTSP/1.0 404 Not Found\r\nCSeq: {cseq}\r\n\r\n".encode()
        if mode == "open":
            return f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\n\r\n".encode()
        auth = re.search(r"Authorization: (.+)", req)
        if auth and self.valid(auth.group(1).strip(), req):
            return f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\nContent-Type: application/sdp\r\n\r\n".encode()
        challenge = f'Digest realm="{REALM}", nonce="{NONCE}"' if mode == "digest" else f'Basic realm="{REALM}"'
        return f"RTSP/1.0 401 Unauthorized\r\nCSeq: {cseq}\r\nWWW-Authenticate: {challenge}\r\n\r\n".encode()

    def valid(self, header: str, req: str) -> bool:
        if header.startswith("Basic "):
            return base64.b64decode(header[6:]).decode() == f"{USER}:{PASSWORD}"
        params = dict(re.findall(r'(\w+)="([^"]*)"', header))
        uri = params.get("uri", "")
        ha1 = hashlib.md5(f"{params.get('username')}:{REALM}:{PASSWORD}".encode()).hexdigest()
        ha2 = hashlib.md5(f"DESCRIBE:{uri}".encode()).hexdigest()
        return params.get("response") == hashlib.md5(f"{ha1}:{NONCE}:{ha2}".encode()).hexdigest()


@pytest.fixture
def rtsp_server() -> Iterator[tuple[type[_Handler], int]]:
    handler = type("H", (_Handler,), {})
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield handler, server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize("mode", ["digest", "basic"])
def test_correct_credentials(rtsp_server, mode):
    handler, port = rtsp_server
    handler.mode = mode
    r = probe_rtsp(f"rtsp://127.0.0.1:{port}/live", USER, PASSWORD, timeout=2)
    assert r.ok and r.status_code == 200


@pytest.mark.parametrize("mode", ["digest", "basic"])
def test_wrong_password_is_reported_as_auth_failure(rtsp_server, mode):
    handler, port = rtsp_server
    handler.mode = mode
    r = probe_rtsp(f"rtsp://127.0.0.1:{port}/live", USER, "wrong", timeout=2)
    assert not r.ok and r.failure == FailureKind.AUTH_FAILED and r.status_code == 401
    assert "wrong" not in r.message


def test_missing_credentials(rtsp_server):
    _, port = rtsp_server
    r = probe_rtsp(f"rtsp://127.0.0.1:{port}/live", timeout=2)
    assert r.failure == FailureKind.AUTH_FAILED


def test_wrong_path(rtsp_server):
    handler, port = rtsp_server
    handler.mode = "notfound"
    assert probe_rtsp(f"rtsp://127.0.0.1:{port}/nope", timeout=2).failure == FailureKind.NOT_FOUND


def test_not_an_rtsp_server(rtsp_server):
    handler, port = rtsp_server
    handler.mode = "garbage"
    assert probe_rtsp(f"rtsp://127.0.0.1:{port}/live", timeout=2).failure == FailureKind.INVALID_SOURCE


def test_connection_refused_is_unreachable():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # closed port
    r = probe_rtsp(f"rtsp://127.0.0.1:{port}/live", timeout=1)
    assert r.failure == FailureKind.UNREACHABLE


def test_invalid_url():
    assert probe_rtsp("http://host/x").failure == FailureKind.INVALID_SOURCE
