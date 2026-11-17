"""Defensive HTTP response headers for a JSON API (MVP 09 security review).

* no MIME sniffing, no framing, no referrer leakage, no browser features;
* API responses are never cached by browsers or proxies (they carry personal / operational data);
* HSTS only when the request reached us over HTTPS (directly or via a TLS-terminating proxy that
  sets ``X-Forwarded-Proto``), so plain-HTTP development keeps working.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import Response

STATIC = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Resource-Policy": "cross-origin",  # the dashboard is served from another origin
}


def apply_security_headers(request: Request, response: Response) -> None:
    for k, v in STATIC.items():
        response.headers.setdefault(k, v)
    path = request.url.path
    if path.startswith("/api/") and "Cache-Control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"
    if path.startswith("/api/"):
        response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    if proto == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
