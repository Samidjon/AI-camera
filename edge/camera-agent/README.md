# Edge Camera Agent

Standalone camera manager that runs next to the cameras (Linux mini PC, NVIDIA Jetson, or a
Windows/macOS workstation for development). **Works fully offline**: no central server, Supabase,
Redis, AI or internet required.

Status: **MVP 02 — Camera Integration.** Capture, camera management and live preview are
implemented. Vision (MVP 03), AI (MVP 04) and the autonomous runtime with sync, systemd and
installers (MVP 05) are not implemented yet.

## Run

```bash
cd edge/camera-agent
cp .env.example .env      # set ACI_AGENT_JWT_SECRET = the central server's ACI_JWT_SECRET
uv sync
uv run python -m aci_agent          # http://127.0.0.1:8100, docs at /docs
```

Data lives in `ACI_AGENT_DATA_DIR` (default `./data`): `agent.db` (SQLite, WAL) and `secret.key`
(encrypts camera credentials). Back them up together.

## Layout

```
aci_agent/
  configuration/   settings (ACI_AGENT_*, ACI_AGENT_STREAM_*)
  camera/          models, source adapters (RTSP/ONVIF/USB/file/FFmpeg), RTSP probe, ONVIF client, CameraManager
  video/           StreamWorker (thread per camera), ConnectionMonitor, bounded frame buffers
  storage/         SQLite database + migrations, repositories
  security/        Fernet secret box, JWT / stream-token verification
  monitoring/      structured logging with credential redaction
  api/             FastAPI management API + MJPEG preview
tests/             unit + integration (fake capture backend, real OpenCV files, optional hardware)
```

## API (prefix `/api/v1`)

| Method | Path | Permission |
|---|---|---|
| GET | `/health`, `/system/status` | public |
| GET | `/cameras`, `/cameras/{id}` | `cameras:read` |
| POST | `/cameras` | `cameras:manage` |
| PATCH / DELETE | `/cameras/{id}` | `cameras:manage` |
| POST | `/cameras/{id}/connect`, `/disconnect`, `/test` | `cameras:control` |
| POST | `/cameras/test` (unsaved input) | `cameras:manage` |
| POST | `/cameras/{id}/stream-token` | `cameras:read` |
| GET | `/cameras/{id}/stream.mjpeg?token=`, `/snapshot.jpg?token=` | stream token |
| GET / POST / PATCH / DELETE | `/camera-groups[/{id}]` | read / manage |

Details: [docs/CAMERA_INTEGRATION.md](../../docs/CAMERA_INTEGRATION.md).

## Rules

- Must not import anything from `apps/api-server`; shared payloads go through `shared/schemas/`.
- No dependency on Redis, Supabase or any network service for capture.
