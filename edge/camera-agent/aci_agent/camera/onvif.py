"""Minimal ONVIF client: resolve the RTSP stream URI of a camera profile.

Implements only what Camera Integration needs (GetCapabilities → GetProfiles → GetStreamUri)
with WS-Security UsernameToken (PasswordDigest), avoiding vendor SDKs. PTZ, events and
WS-Discovery are out of scope for MVP 02.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

import httpx

from aci_agent.camera.models import FailureKind

NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
}


class OnvifError(Exception):
    def __init__(self, message: str, failure: FailureKind) -> None:
        super().__init__(message)
        self.failure = failure


def _security_header(username: str, password: str) -> str:
    if not username:
        return ""
    nonce = os.urandom(16)
    created = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + password.encode()).digest()).decode()  # noqa: S324 — mandated by WS-Security UsernameToken
    return (
        '<s:Header><Security s:mustUnderstand="1" '
        'xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">'
        f"<UsernameToken><Username>{_xml_escape(username)}</Username>"
        '<Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">'
        f"{digest}</Password>"
        '<Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
        f"{base64.b64encode(nonce).decode()}</Nonce>"
        '<Created xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
        f"{created}</Created></UsernameToken></Security></s:Header>"
    )


def _xml_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


class OnvifClient:
    def __init__(
        self,
        host: str,
        port: int = 80,
        username: str = "",
        password: str = "",
        timeout: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9.\-:\[\]]+", host):
            raise OnvifError("Invalid ONVIF host", FailureKind.INVALID_SOURCE)
        self.device_url = f"http://{host}:{port}/onvif/device_service"
        self.username, self.password = username, password
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def close(self) -> None:
        self._client.close()

    def _call(self, url: str, body: str) -> ET.Element:
        envelope = (
            f'<s:Envelope xmlns:s="{NS["s"]}">{_security_header(self.username, self.password)}'
            f"<s:Body>{body}</s:Body></s:Envelope>"
        )
        try:
            resp = self._client.post(
                url, content=envelope, headers={"Content-Type": "application/soap+xml; charset=utf-8"}
            )
        except httpx.TimeoutException as exc:
            raise OnvifError("ONVIF device did not respond (timeout)", FailureKind.UNREACHABLE) from exc
        except httpx.HTTPError as exc:
            raise OnvifError(f"Can't reach ONVIF device: {exc}", FailureKind.UNREACHABLE) from exc
        text = resp.text
        if resp.status_code in (401, 403) or "NotAuthorized" in text:
            raise OnvifError("ONVIF authentication failed — check username/password", FailureKind.AUTH_FAILED)
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise OnvifError("Device returned an invalid ONVIF response", FailureKind.INVALID_SOURCE) from exc
        fault = root.find(".//s:Fault", NS)
        if fault is not None:
            reason = "".join(fault.itertext()).strip()[:200]
            raise OnvifError(f"ONVIF fault: {reason}", FailureKind.UNKNOWN)
        return root

    def media_service_url(self) -> str:
        body = f'<tds:GetCapabilities xmlns:tds="{NS["tds"]}"><tds:Category>Media</tds:Category></tds:GetCapabilities>'
        root = self._call(self.device_url, body)
        xaddr = root.find(".//tt:Media/tt:XAddr", NS)
        return xaddr.text.strip() if xaddr is not None and xaddr.text else self.device_url

    def profile_tokens(self, media_url: str) -> list[str]:
        root = self._call(media_url, f'<trt:GetProfiles xmlns:trt="{NS["trt"]}"/>')
        return [p.get("token") for p in root.findall(".//trt:Profiles", NS) if p.get("token")]

    def stream_uri(self, profile_index: int = 0) -> str:
        """Return the RTSP URI (without credentials) for the given profile."""
        media = self.media_service_url()
        tokens = self.profile_tokens(media)
        if not tokens:
            raise OnvifError("Camera exposes no media profiles", FailureKind.NOT_FOUND)
        if not 0 <= profile_index < len(tokens):
            raise OnvifError(f"Profile #{profile_index} not found ({len(tokens)} available)", FailureKind.NOT_FOUND)
        body = (
            f'<trt:GetStreamUri xmlns:trt="{NS["trt"]}" xmlns:tt="{NS["tt"]}">'
            "<trt:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream>"
            "<tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport></trt:StreamSetup>"
            f"<trt:ProfileToken>{_xml_escape(tokens[profile_index])}</trt:ProfileToken></trt:GetStreamUri>"
        )
        root = self._call(media, body)
        uri = root.find(".//tt:Uri", NS)
        if uri is None or not uri.text:
            raise OnvifError("Camera returned no stream URI", FailureKind.NOT_FOUND)
        return uri.text.strip()
