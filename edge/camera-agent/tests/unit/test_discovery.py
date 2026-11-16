"""Camera discovery against real local sockets: an RTSP server, a non-camera TCP service and an
ONVIF WS-Discovery responder."""

from __future__ import annotations

import socket
import socketserver
import struct
import threading
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from aci_agent.camera.discovery import (
    DiscoveryScanner,
    DiscoveryService,
    parse_probe_match,
    rtsp_options,
    validate_target_network,
    ws_discovery,
)
from tests.conftest import auth, make_token, wait_for

PROBE_MATCH = """<?xml version="1.0"?><s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"><s:Body><d:ProbeMatches><d:ProbeMatch>
<d:Scopes>onvif://www.onvif.org/type/video_encoder onvif://www.onvif.org/name/HIKVISION%20DS-2CD2143
 onvif://www.onvif.org/hardware/DS-2CD2143G0-I onvif://www.onvif.org/location/city/hangzhou</d:Scopes>
<d:XAddrs>http://{ip}:8080/onvif/device_service</d:XAddrs></d:ProbeMatch></d:ProbeMatches></s:Body></s:Envelope>"""


class _Rtsp(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(2)
        try:
            data = self.request.recv(4096).decode()
        except OSError:
            return
        cseq = data.split("CSeq:")[1].split()[0] if "CSeq:" in data else "1"
        self.request.sendall(f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\nServer: Hikvision-Webs\r\n"
                             "Public: OPTIONS, DESCRIBE, SETUP, PLAY\r\n\r\n".encode())  # fmt: skip


class _CameraWeb(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.recv(4096)
        self.request.sendall(
            b"HTTP/1.1 200 OK\r\nServer: App-webs/\r\nContent-Type: text/html\r\n\r\n"
            b"<html><head><title>Network Camera</title></head></html>"
        )


class _NotRtsp(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")


def _serve(handler) -> tuple[socketserver.ThreadingTCPServer, int]:
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


@pytest.fixture
def servers() -> Iterator[tuple[int, int]]:
    a, rtsp_port = _serve(_Rtsp)
    b, other_port = _serve(_NotRtsp)
    yield rtsp_port, other_port
    for s in (a, b):
        s.shutdown()
        s.server_close()


def test_parse_onvif_probe_match():
    info = parse_probe_match(PROBE_MATCH.format(ip="192.168.0.64"))
    assert info.xaddr == "http://192.168.0.64:8080/onvif/device_service" and info.port == 8080
    assert info.name == "HIKVISION DS-2CD2143" and info.hardware == "DS-2CD2143G0-I"
    assert parse_probe_match("<not xml") is None


@pytest.mark.parametrize("cidr", ["8.8.8.0/24", "127.0.0.0/24", "169.254.0.0/24", "10.0.0.0/8"])
def test_only_small_private_networks_can_be_scanned(cidr):
    with pytest.raises(ValueError):
        validate_target_network(cidr)


def test_private_network_accepted():
    assert str(validate_target_network("192.168.1.0/24")) == "192.168.1.0/24"


def test_rtsp_options_confirms_server_without_credentials(servers):
    rtsp_port, other_port = servers
    info = rtsp_options("127.0.0.1", rtsp_port)
    assert info.status == 200 and info.server == "Hikvision-Webs"
    assert rtsp_options("127.0.0.1", other_port) is None  # HTTP is not RTSP


def test_scanner_finds_rtsp_camera_and_ignores_other_services(servers):
    rtsp_port, other_port = servers
    scanner = DiscoveryScanner(rtsp_ports=(rtsp_port, other_port), web_ports=())
    devices = scanner.scan(hosts=["127.0.0.1"], use_ws_discovery=False)
    assert len(devices) == 1
    d = devices[0]
    assert d.rtsp.port == rtsp_port and d.vendor == "hikvision"
    assert d.suggested_source_type == "rtsp"
    assert d.suggested_source_uri == f"rtsp://127.0.0.1:{rtsp_port}/Streaming/Channels/101"


def test_host_with_only_non_camera_ports_is_not_reported(servers):
    _, other_port = servers
    scanner = DiscoveryScanner(rtsp_ports=(other_port,), web_ports=())
    assert scanner.scan(hosts=["127.0.0.1"], use_ws_discovery=False) == []


def test_closed_host_is_fast_and_empty():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    started = time.perf_counter()
    assert DiscoveryScanner(rtsp_ports=(port,), web_ports=()).scan(hosts=["127.0.0.1"], use_ws_discovery=False) == []
    assert time.perf_counter() - started < 3


def test_ws_discovery_multicast_roundtrip():
    """A local ONVIF responder joined to 239.255.255.250:3702 answers the probe."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("", 3702))
        mreq = struct.pack("4s4s", socket.inet_aton("239.255.255.250"), socket.inet_aton("127.0.0.1"))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    except OSError as exc:
        sock.close()
        pytest.skip(f"multicast responder can't bind on this machine: {exc}")
    sock.settimeout(4)

    def responder():
        try:
            _, addr = sock.recvfrom(65535)
            sock.sendto(PROBE_MATCH.format(ip="127.0.0.1").encode(), addr)
        except OSError:
            pass

    threading.Thread(target=responder, daemon=True).start()
    found = ws_discovery(timeout_s=2.0, bind_ip="127.0.0.1")
    sock.close()
    if not found:
        pytest.skip("multicast loopback not delivered on this host")
    # loopback is not a private address, so the result filter drops it unless delivered from a LAN IP
    assert all(info.port == 8080 for info in found.values())


def test_service_marks_already_added_and_single_scan(manager, servers, factory):
    rtsp_port, _ = servers
    from aci_agent.camera.manager import CameraInput
    from aci_agent.camera.models import SourceType

    url = f"rtsp://127.0.0.1:{rtsp_port}/Streaming/Channels/101"
    factory.add(url)
    cam = manager.create_camera(CameraInput(name="Lobby", source_type=SourceType.RTSP, source_uri=url), connect=False)
    svc = DiscoveryService(manager, DiscoveryScanner(rtsp_ports=(rtsp_port,), web_ports=()))
    original = svc.scanner.scan
    svc.scanner.scan = lambda **kw: original(hosts=["127.0.0.1"], use_ws_discovery=False, **{
        k: v for k, v in kw.items() if k in ("progress", "cancel")})  # fmt: skip
    job = svc.start()
    assert wait_for(lambda: job.status == "done", timeout=10)
    assert job.devices[0]["already_added"] is True and job.devices[0]["camera_ids"] == [cam.config.id]


def test_discovery_api_permissions_and_validation(client: TestClient):
    operator = {"Authorization": f"Bearer {make_token(['cameras:read', 'cameras:control'])}"}
    assert client.post("/api/v1/discovery/scans", json={}, headers=operator).status_code == 403
    r = client.post("/api/v1/discovery/scans", json={"network": "8.8.8.0/24"}, headers=auth())
    assert r.status_code == 422
    assert client.get("/api/v1/discovery/networks", headers=auth()).status_code == 200
    assert client.get("/api/v1/discovery/scans/nope", headers=auth()).status_code == 404


def test_camera_found_by_web_interface_when_rtsp_and_onvif_are_off():
    srv, web_port = _serve(_CameraWeb)
    try:
        scanner = DiscoveryScanner(rtsp_ports=(), web_ports=(web_port,))
        devices = scanner.scan(hosts=["127.0.0.1"], use_ws_discovery=False)
    finally:
        srv.shutdown()
        srv.server_close()
    assert len(devices) == 1
    d = devices[0]
    assert d.vendor == "hikvision" and d.web.signature == "app-webs" and d.rtsp is None
    assert "enable RTSP or ONVIF" in d.path_hint


def test_generic_web_interface_is_not_a_camera(servers):
    _, other_port = servers  # plain HTTP server (like a printer or router) without camera signatures
    scanner = DiscoveryScanner(rtsp_ports=(), web_ports=(other_port,))
    assert scanner.scan(hosts=["127.0.0.1"], use_ws_discovery=False) == []
