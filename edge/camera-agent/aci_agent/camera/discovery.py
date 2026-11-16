"""Discovery of cameras on the edge device's local network.

Two standard, non-intrusive methods:

1. **ONVIF WS-Discovery** — a multicast Probe (239.255.255.250:3702) for NetworkVideoTransmitter
   devices; cameras answer with their service address, model and name.
2. **RTSP service scan** — a bounded TCP connect scan of the RTSP ports on the device's own private
   subnets, followed by an unauthenticated RTSP ``OPTIONS`` request to confirm a video server and read
   its ``Server`` header.

Boundaries (by design):
* only private (RFC 1918) IPv4 networks the device is attached to, at most ``MAX_HOSTS`` addresses;
* no credential guessing and no attempt to bypass authentication — discovery finds devices and
  streams; viewing a stream still requires the camera's own username and password;
* bounded concurrency and short timeouts so the scan never floods the network.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import struct
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import psutil

WS_DISCOVERY_ADDR = ("239.255.255.250", 3702)
RTSP_PORTS = (554, 8554)
WEB_PORTS = (80, 8000, 8080)
MAX_HOSTS = 1024
CONNECT_TIMEOUT_S = 0.35
MAX_WORKERS = 128

PROBE = """<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
 xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
 xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
<e:Header><w:MessageID>uuid:{msg_id}</w:MessageID>
<w:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
<w:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action></e:Header>
<e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body></e:Envelope>"""

# Typical main-stream paths by vendor (hint only; the operator confirms in the Add Camera dialog).
VENDOR_PATHS = {
    "hikvision": "/Streaming/Channels/101",
    "dahua": "/cam/realmonitor?channel=1&subtype=0",
    "amcrest": "/cam/realmonitor?channel=1&subtype=0",
    "axis": "/axis-media/media.amp",
    "reolink": "/h264Preview_01_main",
    "uniview": "/media/video1",
    "tp-link": "/stream1",
    "tapo": "/stream1",
    "hanwha": "/profile2/media.smp",
    "foscam": "/videoMain",
}


@dataclass(slots=True)
class OnvifInfo:
    xaddr: str
    port: int
    name: str | None = None
    hardware: str | None = None
    manufacturer: str | None = None


@dataclass(slots=True)
class RtspInfo:
    port: int
    server: str | None = None
    status: int | None = None  # RTSP status code of OPTIONS (200, 401, …)


@dataclass(slots=True)
class WebInfo:
    port: int
    server: str | None = None
    title: str | None = None
    signature: str | None = None  # which camera/NVR signature matched


# Web-interface signatures of IP cameras / NVRs (Server header or page title), lower-case substrings.
WEB_SIGNATURES = {
    "app-webs": "hikvision",
    "dnvrs-webs": "hikvision",
    "hikvision": "hikvision",
    "web service": "dahua",
    "dahua": "dahua",
    "netsurveillance": "xiongmai",
    "network camera": None,
    "ip camera": None,
    "ipcamera": None,
    "nvr": None,
    "dvr": None,
    "reolink": "reolink",
    "axis": "axis",
    "uniview": "uniview",
    "hanwha": "hanwha",
    "wisenet": "hanwha",
}


def http_fingerprint(ip: str, port: int, timeout: float = 2.0) -> WebInfo | None:
    """Plain `GET /` (what a browser does): reads the Server header and page title only."""
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(f"GET / HTTP/1.0\r\nHost: {ip}\r\nUser-Agent: aci-agent\r\n\r\n".encode())
            data = b""
            while len(data) < 16384:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
    except OSError:
        return None
    text = data.decode("latin-1", "replace")
    if not text.startswith("HTTP/"):
        return None
    server = re.search(r"(?im)^server:\s*(.+)$", text)
    title = re.search(r"(?is)<title>\s*(.*?)\s*</title>", text)
    info = WebInfo(
        port,
        server.group(1).strip()[:120] if server else None,
        title.group(1).strip()[:120] if title else None,
    )
    haystack = f"{info.server or ''} {info.title or ''}".lower()
    for sig in WEB_SIGNATURES:
        if sig in haystack:
            info.signature = sig
            break
    return info


@dataclass(slots=True)
class DiscoveredDevice:
    ip: str
    open_ports: list[int] = field(default_factory=list)
    onvif: OnvifInfo | None = None
    rtsp: RtspInfo | None = None
    web: WebInfo | None = None
    vendor: str | None = None
    suggested_source_type: str = "rtsp"
    suggested_source_uri: str = ""
    path_hint: str | None = None
    already_added: bool = False
    camera_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------- networks


def local_private_networks(max_hosts: int = MAX_HOSTS) -> list[tuple[str, ipaddress.IPv4Network]]:
    """(own_ip, network) for each private IPv4 interface. Large networks are narrowed to the /24 around
    the device's own address, so a scan stays local and bounded."""
    out: list[tuple[str, ipaddress.IPv4Network]] = []
    stats = psutil.net_if_stats()
    for name, addrs in psutil.net_if_addrs().items():
        if name in stats and not stats[name].isup:
            continue
        for a in addrs:
            if a.family != socket.AF_INET or not a.netmask:
                continue
            ip = ipaddress.IPv4Address(a.address)
            if not ip.is_private or ip.is_loopback or ip.is_link_local:
                continue
            net = ipaddress.IPv4Network(f"{a.address}/{a.netmask}", strict=False)
            if net.num_addresses > max_hosts:
                net = ipaddress.IPv4Network(f"{a.address}/24", strict=False)
            if all(net != n for _, n in out):
                out.append((a.address, net))
    return out


def validate_target_network(cidr: str, max_hosts: int = MAX_HOSTS) -> ipaddress.IPv4Network:
    """Operator-specified network: must be private and small."""
    net = ipaddress.IPv4Network(cidr, strict=False)
    if not net.is_private or net.is_loopback or net.is_link_local:
        raise ValueError("Only private networks (10.x, 172.16–31.x, 192.168.x) can be scanned")
    if net.num_addresses > max_hosts:
        raise ValueError(f"Network too large: at most {max_hosts} addresses (e.g. a /22)")
    return net


# ---------------------------------------------------------------------- ONVIF WS-Discovery


def parse_probe_match(xml_text: str) -> OnvifInfo | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    xaddrs = scopes = None
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "XAddrs" and el.text:
            xaddrs = el.text.split()
        elif tag == "Scopes" and el.text:
            scopes = el.text.split()
    if not xaddrs:
        return None
    xaddr = next((x for x in xaddrs if "://" in x and not x.startswith("https://[")), xaddrs[0])
    parts = urlsplit(xaddr)
    info = OnvifInfo(xaddr=xaddr, port=parts.port or (443 if parts.scheme == "https" else 80))
    for scope in scopes or []:
        m = re.match(r"onvif://www\.onvif\.org/(name|hardware|mfr|manufacturer)/(.+)", scope)
        if not m:
            continue
        value = m.group(2).replace("%20", " ").replace("_", " ")
        if m.group(1) == "name":
            info.name = value
        elif m.group(1) == "hardware":
            info.hardware = value
        else:
            info.manufacturer = value
    return info


def ws_discovery(timeout_s: float = 3.0, bind_ip: str = "") -> dict[str, OnvifInfo]:
    """Multicast probe; returns {ip: OnvifInfo}."""
    found: dict[str, OnvifInfo] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack("b", 2))
        if bind_ip:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(bind_ip))
        sock.bind((bind_ip, 0))
        sock.settimeout(0.5)
        probe = PROBE.format(msg_id=uuid.uuid4()).encode()
        for _ in range(2):  # UDP: send twice
            sock.sendto(probe, WS_DISCOVERY_ADDR)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                data, (ip, _) = sock.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                break
            info = parse_probe_match(data.decode("utf-8", "replace"))
            if info is not None and ipaddress.IPv4Address(ip).is_private:
                found[ip] = info
    except OSError:
        pass  # multicast not available on this interface: the port scan still runs
    finally:
        sock.close()
    return found


# ---------------------------------------------------------------------- port scan + RTSP probe


def port_open(ip: str, port: int, timeout: float = CONNECT_TIMEOUT_S) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def rtsp_options(ip: str, port: int, timeout: float = 1.5) -> RtspInfo | None:
    """Unauthenticated RTSP OPTIONS: confirms an RTSP server; never sends credentials."""
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(f"OPTIONS rtsp://{ip}:{port}/ RTSP/1.0\r\nCSeq: 1\r\nUser-Agent: aci-agent\r\n\r\n".encode())
            data = b""
            while b"\r\n\r\n" not in data and len(data) < 8192:
                chunk = s.recv(2048)
                if not chunk:
                    break
                data += chunk
    except OSError:
        return None
    text = data.decode("latin-1", "replace")
    m = re.match(r"RTSP/\d\.\d\s+(\d{3})", text)
    if not m:
        return None
    server = re.search(r"(?im)^server:\s*(.+)$", text)
    return RtspInfo(port=port, server=server.group(1).strip()[:120] if server else None, status=int(m.group(1)))


def guess_vendor(*texts: str | None) -> str | None:
    joined = " ".join(t for t in texts if t).lower()
    for vendor in VENDOR_PATHS:
        if vendor in joined:
            return vendor
    return None


class DiscoveryScanner:
    def __init__(self, rtsp_ports: tuple[int, ...] = RTSP_PORTS, web_ports: tuple[int, ...] = WEB_PORTS) -> None:
        self.rtsp_ports = rtsp_ports
        self.web_ports = web_ports

    def scan(
        self,
        networks: list[ipaddress.IPv4Network] | None = None,
        hosts: list[str] | None = None,
        use_ws_discovery: bool = True,
        progress: callable | None = None,
        cancel: threading.Event | None = None,
    ) -> list[DiscoveredDevice]:
        own_ips = set()
        if networks is None and hosts is None:
            pairs = local_private_networks()
            networks = [n for _, n in pairs]
            own_ips = {ip for ip, _ in pairs}
        targets: list[str] = list(hosts or [])
        for net in networks or []:
            targets.extend(str(h) for h in net.hosts() if str(h) not in own_ips)
        targets = list(dict.fromkeys(targets))[:MAX_HOSTS]

        onvif: dict[str, OnvifInfo] = {}
        ws_thread = None
        if use_ws_discovery:

            def _ws() -> None:
                for own in own_ips or {""}:
                    onvif.update(ws_discovery(bind_ip=own))

            ws_thread = threading.Thread(target=_ws, daemon=True)
            ws_thread.start()

        ports = self.rtsp_ports + self.web_ports
        jobs = [(ip, p) for ip in targets for p in ports]
        open_ports: dict[str, list[int]] = {}
        done = 0
        lock = threading.Lock()

        def check(job: tuple[str, int]) -> None:
            nonlocal done
            if cancel is not None and cancel.is_set():
                return
            ip, port = job
            if port_open(ip, port):
                with lock:
                    open_ports.setdefault(ip, []).append(port)
            with lock:
                done += 1
                if progress and done % 64 == 0:
                    progress(done / max(1, len(jobs)))

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            list(pool.map(check, jobs))
        if ws_thread is not None:
            ws_thread.join(5)

        devices: list[DiscoveredDevice] = []
        web_hits: dict[str, WebInfo] = {}
        for ip, ps in open_ports.items():
            for port in [p for p in self.web_ports if p in ps]:
                web = http_fingerprint(ip, port)
                if web is not None and web.signature:
                    web_hits[ip] = web
                    break
        rtsp_hosts = {ip for ip, ps in open_ports.items() if set(ps) & set(self.rtsp_ports)}
        candidates = set(onvif) | rtsp_hosts | set(web_hits)
        for ip in sorted(candidates, key=lambda x: tuple(int(p) for p in x.split("."))):
            ps = sorted(open_ports.get(ip, []))
            rtsp = None
            for port in [p for p in self.rtsp_ports if p in ps]:
                rtsp = rtsp_options(ip, port)
                if rtsp:
                    break
            info = onvif.get(ip)
            web = web_hits.get(ip)
            if rtsp is None and info is None and web is None:
                continue  # not an RTSP server, no ONVIF answer, no camera web interface: not a camera
            vendor = guess_vendor(
                info.manufacturer if info else None,
                info.hardware if info else None,
                info.name if info else None,
                rtsp.server if rtsp else None,
                web.server if web else None,
                web.title if web else None,
            ) or (WEB_SIGNATURES.get(web.signature) if web and web.signature else None)
            dev = DiscoveredDevice(ip=ip, open_ports=ps, onvif=info, rtsp=rtsp, web=web, vendor=vendor)
            path = VENDOR_PATHS.get(vendor or "", "/")
            if info is not None:
                dev.suggested_source_type = "onvif"
                dev.suggested_source_uri = f"{ip}:{info.port}"
            elif rtsp is not None:
                dev.suggested_source_uri = f"rtsp://{ip}:{rtsp.port}{path}"
                dev.path_hint = None if vendor else "Stream path unknown — check the camera manual"
            else:
                dev.suggested_source_uri = f"rtsp://{ip}:554{path}"
                dev.path_hint = (
                    "Camera web interface found, but RTSP/ONVIF did not answer — enable RTSP or ONVIF "
                    "in the camera's own settings first"
                )
            devices.append(dev)
        if progress:
            progress(1.0)
        return devices


@dataclass(slots=True)
class ScanJob:
    id: str
    status: str = "running"  # running | done | failed | cancelled
    progress: float = 0.0
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    networks: list[str] = field(default_factory=list)
    devices: list[dict] = field(default_factory=list)
    error: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)


class DiscoveryService:
    """One scan at a time, run in the background; results kept for the latest few jobs."""

    def __init__(self, camera_manager, scanner: DiscoveryScanner | None = None) -> None:
        self._cameras = camera_manager
        self.scanner = scanner or DiscoveryScanner()
        self._jobs: dict[str, ScanJob] = {}
        self._lock = threading.Lock()

    def start(self, network: str | None = None, hosts: list[str] | None = None) -> ScanJob:
        with self._lock:
            if any(j.status == "running" for j in self._jobs.values()):
                raise RuntimeError("A network scan is already running")
            nets = [validate_target_network(network)] if network else None
            job = ScanJob(id=uuid.uuid4().hex[:12])
            job.networks = [str(n) for n in nets] if nets else [str(n) for _, n in local_private_networks()]
            self._jobs[job.id] = job
            for old in sorted(self._jobs.values(), key=lambda j: j.started_at)[:-5]:
                self._jobs.pop(old.id, None)
        threading.Thread(target=self._run, args=(job, nets, hosts), name=f"discovery-{job.id}", daemon=True).start()
        return job

    def _run(self, job: ScanJob, nets, hosts) -> None:
        try:
            devices = self.scanner.scan(networks=nets, hosts=hosts, cancel=job.cancel,
                                        progress=lambda p: setattr(job, "progress", round(p, 3)))  # fmt: skip
            existing = self._existing_hosts()
            for d in devices:
                d.camera_ids = existing.get(d.ip, [])
                d.already_added = bool(d.camera_ids)
            job.devices = [d.to_dict() for d in devices]
            job.status = "cancelled" if job.cancel.is_set() else "done"
        except Exception as exc:  # noqa: BLE001
            job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"[:200]
        finally:
            job.progress = 1.0
            job.finished_at = time.time()

    def _existing_hosts(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for cam in self._cameras.repo.list():
            src = cam.source_uri
            host = urlsplit(src).hostname if "://" in src else src.rsplit(":", 1)[0] if ":" in src else src
            if host:
                out.setdefault(host, []).append(cam.id)
        return out

    def get(self, job_id: str) -> ScanJob | None:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> ScanJob | None:
        job = self._jobs.get(job_id)
        if job is not None and job.status == "running":
            job.cancel.set()
        return job

    def latest(self) -> ScanJob | None:
        return max(self._jobs.values(), key=lambda j: j.started_at, default=None)
