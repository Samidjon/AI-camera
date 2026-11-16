# Camera Integration (MVP 02)

Cameras are managed by the **Edge Camera Agent** (`edge/camera-agent`), a standalone process that
runs next to the cameras. It works without the central server, Supabase, Redis, AI or internet.

## Supported sources

| Type | Source field | Credentials | How it's opened |
|---|---|---|---|
| **RTSP** | `rtsp://host[:554]/path` (`rtsps://` too) | optional, encrypted | OpenCV + FFmpeg, RTSP over TCP, open/read timeouts |
| **ONVIF** | `host[:port]` (default 80) | usually required | ONVIF `GetCapabilities → GetProfiles → GetStreamUri` (WS-Security digest), then RTSP |
| **USB** | device index `0`–`63` | — | DirectShow (Windows), V4L2 (Linux), AVFoundation (macOS) |
| **File** | path on the edge device (`.mp4 .mkv .avi .mov .webm .ts …`) | — | FFmpeg, paced to real time, optional loop |
| **Stream (FFmpeg)** | `http(s)://`, `rtmp://`, `srt://`, `udp://`, `rtp://`, `tcp://` | optional | OpenCV + FFmpeg |

Only standard protocols are used — no vendor SDKs.

### Common RTSP paths (check your camera's manual)

| Vendor | Main stream example |
|---|---|
| Hikvision | `rtsp://IP:554/Streaming/Channels/101` |
| Dahua / Amcrest | `rtsp://IP:554/cam/realmonitor?channel=1&subtype=0` |
| Axis | `rtsp://IP/axis-media/media.amp` |
| Reolink | `rtsp://IP:554/h264Preview_01_main` |
| Generic ONVIF | use the **ONVIF** type and let the agent discover the URI |

If you paste `rtsp://user:pass@host/...`, the credentials are moved to encrypted storage and removed
from the URL automatically.

## Network discovery (scan for cameras)

Cameras → **Scan network** (permission `cameras:manage`). The edge agent looks for cameras on its own
local network with three non-intrusive methods:

| Method | What it does |
|---|---|
| ONVIF WS-Discovery | Multicast Probe to 239.255.255.250:3702; ONVIF cameras answer with their service address, name and model |
| RTSP service scan | TCP connect to ports 554 / 8554, then an **unauthenticated** RTSP `OPTIONS` to confirm a video server and read its `Server` header |
| Web-interface fingerprint | For hosts with 80 / 8000 / 8080 open: one `GET /` (like a browser) — known camera / NVR server headers and titles (Hikvision, Dahua, Reolink, Axis, …). Finds cameras whose RTSP / ONVIF is switched off |

Results suggest a source (ONVIF `host:port`, or `rtsp://host:port/<vendor path>`) and open the Add
Camera dialog pre-filled; cameras already added are marked.

**Boundaries:** only private networks (10/8, 172.16/12, 192.168/16) the device is attached to, or one
you enter (at most 1024 addresses); bounded concurrency and 0.35 s connect timeouts; **no credential
guessing and no authentication bypass** — viewing a stream still requires the camera's own username
and password. A typical /24 scan takes 4–10 s. Verified on the development network: 4 hosts answered
(router, printer, two other devices), none of them a camera, and none was misreported as one.

API: `GET /api/v1/discovery/networks`, `POST /api/v1/discovery/scans` (`{"network": "192.168.1.0/24"}`
optional), `GET /api/v1/discovery/scans/{id}`, `POST /api/v1/discovery/scans/{id}/cancel`.

## Camera status

Status reflects **real frame flow**, not just an opened socket.

| Status | Meaning |
|---|---|
| `online` | Frames are arriving (within `ACI_AGENT_STREAM_STALE_AFTER_S`, default 4 s) |
| `connecting` | First connection attempt |
| `reconnecting` | Stream lost, stalled or host unreachable — retrying with exponential backoff (1 s → 30 s, jitter) |
| `error` | Non-transient: wrong credentials, wrong path / missing file / missing USB device, undecodable format. Still retried slowly (≥10 s), so it recovers when fixed |
| `offline` | Disconnected by the operator, or a non-looping file ended |
| `disabled` | Disabled in the configuration |

Failure kinds reported with the status: `unreachable`, `auth_failed`, `not_found`, `invalid_source`,
`unsupported_format`, `stream_lost`, `unknown`. For RTSP, the agent runs its own RTSP `DESCRIBE`
probe (Basic/Digest) when FFmpeg can't open a stream, to tell "wrong password" (401) from
"wrong path" (404) from "host down".

## Stream processing

```
camera ─► CaptureDevice (OpenCV/FFmpeg) ─► StreamWorker thread (one per camera)
                                              │  ConnectionMonitor: measured FPS, stall detection
                                              ├─► LatestFrameBuffer (1 frame)  ─► MJPEG preview / snapshot
                                              └─► FrameQueue × N (bounded, drop-oldest) ─► analysis (MVP 03)
```

- One thread per camera; every exception is contained inside it — one failing camera never
  affects the others (tested with 8 concurrent sources).
- All buffers are bounded: the preview buffer holds one frame; analysis queues drop the oldest
  frame when full (`ACI_AGENT_STREAM_FRAME_QUEUE_SIZE`, default 4).
- The capture device is released in `finally` on every exit path; disconnect / delete join the
  thread (tested: no devices left open after reconnect cycles).
- `max_fps` per camera caps frames delivered to consumers; the reader keeps draining the source.

## Live preview

`POST /cameras/{id}/stream-token` returns a short-lived (default 120 s), camera-scoped token; the
dashboard opens `GET /cameras/{id}/stream.mjpeg?token=…` — a single long-lived
`multipart/x-mixed-replace` response the agent pushes frames into (≤10 fps, ≤960 px wide by
default). The user's access token never appears in a URL. Frames are JPEG-encoded once per frame
and shared by all viewers.

**Limitation (MVP 02):** browsers allow ~6 HTTP/1.1 connections per host, so the Cameras page runs
live previews for at most 4 cameras at a time (others show "Show live view"). Multi-camera live
walls with a multiplexed transport are part of the monitoring screen (MVP 07).

## Security

- Credentials are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) in a separate
  `camera_secrets` table. The key is `ACI_AGENT_SECRET_KEY` or `<data_dir>/secret.key`
  (created with owner-only permissions). **Back up the key together with the data directory.**
- The API never returns credentials (`has_credentials: true/false` only); validation errors never
  echo input; logs redact `user:pass@` and `password=` patterns.
- Management API: dashboard JWTs (issued by the central server) are verified locally with the
  shared secret. Permissions: `cameras:read` (view), `cameras:control` (connect/disconnect/test),
  `cameras:manage` (add/edit/delete/groups). Built-in roles: administrator = all,
  operator = read + control, viewer = read.
- File and stream inputs are validated (no `file://` through the stream type, whitelisted video
  extensions, bounded lengths, no control characters).

## Testing

```bash
cd edge/camera-agent
uv run pytest                                   # 82 tests, no hardware needed
ACI_TEST_USB_INDEX=0 uv run pytest -m hardware  # real USB webcam + file simultaneously
ACI_TEST_RTSP_URL=rtsp://user:pass@IP/path uv run pytest -m rtsp
```

| Scenario from the MVP 02 spec | Test |
|---|---|
| Camera connection | `test_camera_connects_and_reports_real_stream_state`, real files, real webcam |
| Invalid URL | `test_invalid_url_is_rejected_before_saving`, `test_invalid_sources_rejected` |
| Wrong password | `test_wrong_password_is_an_error_not_online`, RTSP probe vs. a real RTSP TCP server (Basic + Digest) |
| Cable unplugged | `test_cable_unplug_then_replug_recovers` |
| Network loss / recovery | `test_unreachable_host_retries_with_backoff`, `test_reconnect_cycles_do_not_leak_devices`, `test_stalled_stream_is_not_reported_online` |
| Invalid video format | `test_undecodable_stream_reports_unsupported_format`, `test_corrupt_video_is_unsupported_format` |
| Concurrent cameras | `test_one_failing_camera_does_not_affect_others`, `test_many_cameras_have_independent_workers`, `test_two_video_files_stream_simultaneously`, `test_real_usb_camera_with_file_simultaneously` |
| Resource release | `test_disconnect_releases_resources`, `test_reconnect_cycles_do_not_leak_devices` |

Failure scenarios that can't be triggered on demand with real hardware (wrong password on a real
camera, pulling a cable) are reproduced with a controllable capture backend; the RTSP
diagnostics are tested against a real local RTSP TCP server. **Not verified on a physical IP
camera in this environment** — run the `rtsp` marker against your camera.
