import httpx
import pytest

from aci_agent.camera.models import FailureKind
from aci_agent.camera.onvif import OnvifClient, OnvifError

MEDIA = "http://cam:80/onvif/media_service"


def _soap(body: str) -> str:
    return (
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
        'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
        f'xmlns:tt="http://www.onvif.org/ver10/schema"><s:Body>{body}</s:Body></s:Envelope>'
    )


def _transport(require_password: str | None = "secret", seen: list | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        if seen is not None:
            seen.append(body)
        if require_password and "UsernameToken" not in body:
            return httpx.Response(401, text="")
        if "GetCapabilities" in body:
            return httpx.Response(200, text=_soap(f"<tds:GetCapabilitiesResponse><tds:Capabilities><tt:Media><tt:XAddr>{MEDIA}</tt:XAddr></tt:Media></tds:Capabilities></tds:GetCapabilitiesResponse>"))
        if "GetProfiles" in body:
            return httpx.Response(200, text=_soap('<trt:GetProfilesResponse><trt:Profiles token="main"/><trt:Profiles token="sub"/></trt:GetProfilesResponse>'))
        if "GetStreamUri" in body:
            token = "sub" if ">sub<" in body else "main"
            return httpx.Response(200, text=_soap(f"<trt:GetStreamUriResponse><trt:MediaUri><tt:Uri>rtsp://cam:554/{token}</tt:Uri></trt:MediaUri></trt:GetStreamUriResponse>"))
        return httpx.Response(500, text="")

    return httpx.MockTransport(handler)


def test_resolves_stream_uri_with_ws_security():
    seen: list[str] = []
    client = OnvifClient("cam", 80, "admin", "secret", transport=_transport(seen=seen))
    assert client.stream_uri(0) == "rtsp://cam:554/main"
    assert client.stream_uri(1) == "rtsp://cam:554/sub"
    assert all("PasswordDigest" in b for b in seen)
    assert all("secret" not in b for b in seen)  # only the digest travels, never the password


def test_auth_failure():
    client = OnvifClient("cam", 80, "", "", transport=_transport())
    with pytest.raises(OnvifError) as err:
        client.stream_uri()
    assert err.value.failure == FailureKind.AUTH_FAILED


def test_missing_profile():
    client = OnvifClient("cam", 80, "admin", "secret", transport=_transport())
    with pytest.raises(OnvifError) as err:
        client.stream_uri(5)
    assert err.value.failure == FailureKind.NOT_FOUND


def test_unreachable():
    def boom(request):
        raise httpx.ConnectError("refused")

    client = OnvifClient("cam", 80, transport=httpx.MockTransport(boom))
    with pytest.raises(OnvifError) as err:
        client.stream_uri()
    assert err.value.failure == FailureKind.UNREACHABLE


def test_host_injection_rejected():
    with pytest.raises(OnvifError):
        OnvifClient("cam/evil?x=", 80)
