"""HTTP client for the central server's device API.

The wire contract (implemented by the central server in MVP 06) is documented in
``docs/EDGE_AGENT_GUIDE.md`` → "Server contract". All calls are plain JSON over HTTPS; the device
authenticates with ``Authorization: Bearer <device secret>`` plus ``X-ACI-Device-ID``.

Error mapping:

* transport problems, timeouts, 5xx, 429  → :class:`ServerUnavailable` (retry with backoff)
* 401 / 403 on an authenticated call      → :class:`DeviceRevoked` (stop syncing, keep working)
* other 4xx                               → :class:`ServerRejected` (request is wrong; not retried as is)
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from aci_agent import __version__


class ServerError(Exception):
    pass


class ServerUnavailable(ServerError):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class DeviceRevoked(ServerError):
    pass


class ServerRejected(ServerError):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class InsecureServerURL(ValueError):
    pass


def validate_server_url(url: str, allow_insecure_http: bool) -> str:
    """Normalize the server base URL; refuse plain HTTP unless explicitly allowed."""
    url = url.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise ValueError("Server URL must look like https://server.example.com")
    if parts.username or parts.password:
        raise ValueError("Server URL must not contain credentials")
    if parts.query or parts.fragment:
        raise ValueError("Server URL must not contain a query or fragment")
    if parts.scheme == "http" and not allow_insecure_http:
        raise InsecureServerURL(
            "HTTPS is required for the central server (set ACI_AGENT_SERVER_ALLOW_INSECURE_HTTP=true "
            "only for development or an isolated lab network)"
        )
    return url


def is_loopback(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _error_text(response: httpx.Response) -> tuple[str, str | None]:
    try:
        body = response.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            return str(err.get("message") or response.reason_phrase)[:200], err.get("code")
        if isinstance(body, dict) and "detail" in body:
            return str(body["detail"])[:200], None
    except ValueError:
        pass
    return f"HTTP {response.status_code}", None


class ServerClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 10.0,
        ca_file: Path | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url
        verify: bool | str = str(ca_file) if ca_file else True
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout_s,
            verify=verify,
            transport=transport,
            follow_redirects=False,  # never follow a redirect to another (possibly http://) host with credentials
            headers={"User-Agent": f"aci-edge-agent/{__version__}"},
        )
        self.device_id: str | None = None
        self._secret: str | None = None

    def set_credentials(self, device_id: str, secret: str) -> None:
        self.device_id, self._secret = device_id, secret

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------ transport

    def _request(self, method: str, path: str, *, authenticated: bool = True, **kwargs) -> dict:
        headers = kwargs.pop("headers", {})
        if authenticated:
            if not self._secret or not self.device_id:
                raise DeviceRevoked("Device has no credentials")
            headers["Authorization"] = f"Bearer {self._secret}"
            headers["X-ACI-Device-ID"] = self.device_id
        try:
            response = self._http.request(method, path, headers=headers, **kwargs)
        except httpx.TimeoutException as exc:
            raise ServerUnavailable(f"Timeout talking to the server ({type(exc).__name__})") from None
        except httpx.TransportError as exc:
            # Includes TLS verification failures, DNS errors, refused connections.
            raise ServerUnavailable(f"Server unreachable: {type(exc).__name__}: {str(exc)[:160]}") from None
        if response.status_code in (401, 403) and authenticated:
            message, _code = _error_text(response)
            raise DeviceRevoked(message)
        if response.status_code == 429 or response.status_code >= 500:
            retry_after = None
            try:
                retry_after = float(response.headers.get("Retry-After", ""))
            except ValueError:
                pass
            raise ServerUnavailable(f"Server busy or failing (HTTP {response.status_code})", retry_after)
        if 300 <= response.status_code < 400:
            raise ServerRejected(f"Unexpected redirect (HTTP {response.status_code})", response.status_code)
        if response.status_code >= 400:
            message, _code = _error_text(response)
            raise ServerRejected(message, response.status_code)
        if response.status_code == 204 or not response.content:
            return {}
        try:
            data = response.json()
        except ValueError:
            raise ServerRejected("Server returned invalid JSON", response.status_code) from None
        return data if isinstance(data, dict) else {"data": data}

    # ------------------------------------------------------------------ device API

    def register(self, enrollment_token: str, payload: dict) -> dict:
        return self._request(
            "POST",
            "/api/v1/devices/register",
            authenticated=False,
            json=payload,
            headers={"Authorization": f"Bearer {enrollment_token}"},
        )

    def heartbeat(self, payload: dict) -> dict:
        # The device is identified by its credential + X-ACI-Device-ID header.
        return self._request("POST", "/api/v1/devices/heartbeat", json=payload)

    def rotate_credentials(self) -> dict:
        return self._request("POST", "/api/v1/devices/credentials/rotate")

    def upload_events(self, events: list[dict]) -> dict:
        return self._request("POST", "/api/v1/events/batch", json={"device_id": self.device_id, "events": events})

    def get_configuration(self) -> dict:
        return self._request("GET", f"/api/v1/devices/{self.device_id}/configuration")

    def ack_configuration(self, payload: dict) -> dict:
        return self._request("POST", f"/api/v1/devices/{self.device_id}/configuration/ack", json=payload)
