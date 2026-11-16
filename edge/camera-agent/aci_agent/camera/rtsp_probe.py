"""RTSP diagnostics. OpenCV/FFmpeg only report "can't open"; this probe speaks RTSP
(DESCRIBE with Basic/Digest auth) to tell the operator WHY: host unreachable, wrong
credentials, or wrong stream path."""

from __future__ import annotations

import base64
import hashlib
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from aci_agent.camera.models import FailureKind


@dataclass(slots=True)
class ProbeResult:
    ok: bool
    failure: FailureKind | None
    message: str
    status_code: int | None = None


def _strip_credentials(url: str) -> str:
    parts = urlsplit(url)
    netloc = parts.hostname or ""
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))


def _read_response(sock: socket.socket) -> tuple[int, dict[str, str]]:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
        if len(data) > 65536:
            break
    head = data.split(b"\r\n\r\n", 1)[0].decode("latin-1")
    lines = head.split("\r\n")
    match = re.match(r"RTSP/\d\.\d\s+(\d{3})", lines[0] if lines else "")
    if not match:
        raise ValueError("Not an RTSP response")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            # keep the first WWW-Authenticate (servers may send Digest and Basic)
            headers.setdefault(key.strip().lower(), value.strip())
    return int(match.group(1)), headers


def _digest_header(challenge: str, username: str, password: str, method: str, uri: str) -> str:
    params = dict(re.findall(r'(\w+)="?([^",]+)"?', challenge))
    realm, nonce = params.get("realm", ""), params.get("nonce", "")
    ha1 = hashlib.md5(f"{username}:{realm}:{password}".encode()).hexdigest()  # noqa: S324 — RTSP Digest mandates MD5
    ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()  # noqa: S324
    response = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()  # noqa: S324
    return f'Digest username="{username}", realm="{realm}", nonce="{nonce}", uri="{uri}", response="{response}"'


def probe_rtsp(url: str, username: str = "", password: str = "", timeout: float = 5.0) -> ProbeResult:
    parts = urlsplit(url)
    if parts.scheme.lower() != "rtsp" or not parts.hostname:
        return ProbeResult(False, FailureKind.INVALID_SOURCE, "URL must look like rtsp://host[:port]/path")
    host, port = parts.hostname, parts.port or 554
    clean = _strip_credentials(url)

    def describe(sock: socket.socket, cseq: int, auth: str | None) -> tuple[int, dict[str, str]]:
        req = f"DESCRIBE {clean} RTSP/1.0\r\nCSeq: {cseq}\r\nAccept: application/sdp\r\nUser-Agent: aci-agent\r\n"
        if auth:
            req += f"Authorization: {auth}\r\n"
        sock.sendall((req + "\r\n").encode("latin-1"))
        return _read_response(sock)

    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            code, headers = describe(sock, 1, None)
            if code == 401:
                if not (username or password):
                    return ProbeResult(False, FailureKind.AUTH_FAILED, "Camera requires a username and password", 401)
                challenge = headers.get("www-authenticate", "")
                if challenge.lower().startswith("digest"):
                    auth = _digest_header(challenge, username, password, "DESCRIBE", clean)
                else:
                    auth = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
                code, headers = describe(sock, 2, auth)
    except TimeoutError:
        return ProbeResult(False, FailureKind.UNREACHABLE, f"No response from {host}:{port} (timeout)")
    except ConnectionRefusedError:
        return ProbeResult(
            False, FailureKind.UNREACHABLE, f"Connection refused by {host}:{port} — wrong port or RTSP disabled"
        )
    except OSError as exc:
        return ProbeResult(False, FailureKind.UNREACHABLE, f"Can't reach {host}:{port}: {exc.strerror or exc}")
    except ValueError:
        return ProbeResult(False, FailureKind.INVALID_SOURCE, f"{host}:{port} does not speak RTSP")

    if code == 200:
        return ProbeResult(True, None, "RTSP stream description received", code)
    if code == 401:
        return ProbeResult(False, FailureKind.AUTH_FAILED, "Wrong username or password", code)
    if code == 403:
        return ProbeResult(False, FailureKind.AUTH_FAILED, "Access forbidden for this user", code)
    if code == 404:
        return ProbeResult(False, FailureKind.NOT_FOUND, "Stream path not found on the camera", code)
    return ProbeResult(False, FailureKind.UNKNOWN, f"Camera answered RTSP {code}", code)
