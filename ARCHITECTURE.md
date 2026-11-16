# Architecture

## System overview

```
 IP / USB cameras
        │ RTSP · ONVIF · USB · files
        ▼
 ┌──────────────────────────────┐      HTTPS (optional, MVP 05–06)      ┌───────────────────────────┐
 │ Edge Camera Agent  (MVP 02–05)│ ───────── event batches ────────────▶ │ Central API Server        │
 │  capture → standard vision    │ ◀──────── configuration ───────────── │  FastAPI  (apps/api-server)│
 │  (+ optional AI) → SQLite     │                                       │      │                    │
 │  works fully offline          │                                       │      ▼                    │
 └──────────────────────────────┘                                       │ PostgreSQL (Supabase)     │
                                                                         │ Redis (optional)          │
                                                                         └────────────┬──────────────┘
                                                                                      │ REST (+ WebSocket MVP 06)
                                                                                      ▼
                                                                         ┌───────────────────────────┐
                                                                         │ Dashboard (apps/dashboard)│
                                                                         │ Next.js control center    │
                                                                         └───────────────────────────┘
```

Dependency direction is one-way: **edge never depends on the server**; the server never depends on
an edge being online; the dashboard only talks to the server's public API.

## Modules

### apps/api-server

| Layer | Path | Responsibility |
|---|---|---|
| Config | `app/core/config.py` | `ACI_*` environment variables (pydantic-settings), Supabase URL normalisation, production guards (secret length, DB required) |
| Logging | `app/core/logging.py` | JSON logs, `request_id` correlation, credential redaction (URL passwords, `password=`, tokens) |
| Errors | `app/core/errors.py` | `AppError` hierarchy + handlers → one error envelope for every failure |
| Security | `app/core/security.py` | bcrypt hashing, JWT access tokens |
| DB | `app/db/` | Declarative base with naming conventions, lazy engine (API starts without a DB and reports it), sessions |
| Models | `app/models/` | users, roles, cameras, camera_groups, camera_events, system_logs, audit_logs |
| Services | `app/services/` | Health checks (isolated, never raise), authentication + audit |
| API | `app/api/v1/` | Versioned routers; `deps.py` holds auth dependencies and the permission guard |
| Migrations | `alembic/` | Hand-written, dialect-portable migrations (PostgreSQL + SQLite) |
| CLI | `app/cli.py` | `create-user`, `check-db` |
| Devices (MVP 06) | `app/services/devices.py`, `app/services/device_config.py`, `app/api/device_auth.py` | Enrollment, device auth (hashed secrets), heartbeat → status, camera registry sync, rotation, revocation; versioned configuration with conflicts, ack, rollback |
| Event Processor (MVP 06) | `app/processing/event_processor.py` | Per-event validation, device rights, idempotent insert (`ON CONFLICT DO NOTHING RETURNING`), hourly aggregates — independent of HTTP |
| Real-time (MVP 06) | `app/realtime/hub.py`, `app/api/v1/endpoints/realtime.py` | WebSocket fan-out with per-permission filtering and bounded per-client queues; offline monitor in `app/main.py` |
| Rate limiting | `app/core/ratelimit.py` | In-process token buckets per device / IP, 429 + Retry-After |

### apps/dashboard

| Path | Responsibility |
|---|---|
| `src/lib/api/` | Typed API client: timeouts, error-envelope parsing, `NetworkError` vs `ApiError` |
| `src/lib/auth/` | Session state (`loading` / `anonymous` / `authenticated` / `server_unreachable`) |
| `src/lib/system/` | One shared `/system/status` poller for the whole console |
| `src/components/layout/` | App shell: sidebar, top bar, server link status, clock, user menu |
| `src/components/common/` | Reusable console primitives: Panel, MetricTile, StatusIndicator, PageHeader, ModulePending |
| `src/components/ui/` | shadcn/ui primitives (radix-nova preset, Lucide icons) |
| `src/app/(console)/` | Authenticated pages: Overview, Cameras, Devices, Events, Analytics, Settings |
| `src/app/login/` | Login |

### edge/camera-agent (MVP 02)

Standalone process next to the cameras; its own SQLite DB and local API. Details:
[docs/CAMERA_INTEGRATION.md](docs/CAMERA_INTEGRATION.md).

| Path | Responsibility |
|---|---|
| `camera/sources.py` | Input validation, credential splitting, `CaptureFactory` (OpenCV/FFmpeg) per source type |
| `camera/rtsp_probe.py`, `camera/onvif.py` | RTSP DESCRIBE diagnostics, ONVIF stream-URI discovery |
| `camera/manager.py` | Camera Manager: CRUD, connect/disconnect, test, groups, worker lifecycle |
| `video/stream_worker.py` | One thread per camera: connect → read → detect loss → backoff → reconnect |
| `video/connection_monitor.py` | Measured FPS, stall detection (status from real frame flow) |
| `video/frame_buffer.py` | Bounded buffers: latest-frame (preview), drop-oldest queues (analysis) |
| `storage/` | SQLite (WAL, `user_version` migrations): cameras, groups, encrypted secrets |
| `security/` | Fernet secret box; local JWT verification; camera-scoped stream tokens |
| `api/app.py` | Management API + MJPEG preview |
| `standard_engine/` | Motion detectors, geometry, zones/lines rules, VisionWorker pipeline, overlays (MVP 03) |
| `tracking/` | Temporary motion-object tracker (MVP 03) |
| `events/` | Event model + local event database with async writer, retention, recovery (MVP 03) |
| `monitoring/resources.py` | Process CPU / memory sampling, memory-limit protection (MVP 03) |
| `ai_engine/` | Optional AI: provider interface, YOLOX on OpenCV DNN, model registry, ByteTrack, attributes, supervisor (MVP 04) |
| `agent/` | Device identity, hardware facts, backup scheduler (MVP 05) |
| `networking/`, `synchronization/` | Central-server client (HTTPS), encrypted server link, Network Manager: registration, heartbeat, batched idempotent upload, config sync, backoff, revocation (MVP 05) |
| `monitoring/hardware.py`, `monitoring/system_log.py` | Hardware metrics (CPU/RAM/disk/temperature/GPU/network); system log in SQLite (MVP 05) |
| `storage/backup.py`, `deploy/` | Online backups + restore; Linux installer, systemd unit, `aci-agentctl` (MVP 05) |

### shared/

OpenAPI contract (exported from the server) and JSON schemas for payloads that cross process
boundaries (`camera-event.schema.json`).

## Key decisions

| # | Decision | Reason |
|---|---|---|
| D1 | **No Docker in MVP 01; PostgreSQL on Supabase** (owner's decision, 2026-11-16) | Simpler local setup on Windows; managed Postgres. Packaging revisited in MVP 09. |
| D2 | **Own auth tables, not Supabase Auth** | Offline-first: auth must not depend on a cloud identity service; edge and on-prem installs need it too. |
| D3 | **RLS enabled on every table** | Supabase exposes `public` via its REST API. RLS without policies closes that path; the server connects as owner. |
| D4 | **Redis optional** | Not available without Docker on Windows; never required by edge. Status reports `not_configured`. |
| D5 | **`camera_events.id` = source event UUID** | Idempotent sync from edge (MVP 05–06): re-uploads can't duplicate. |
| D6 | **Events keep history when cameras/users are deleted** (`ON DELETE SET NULL`) | Audit and analytics integrity. |
| D7 | **Camera secrets not in `cameras`** | MVP 02 adds a separate encrypted credential store. |
| D8 | **Lenient email format** | On-prem installs use intranet domains (`*.local`) that strict validators reject. |
| D9 | **Liveness ≠ readiness** | `/health` never touches dependencies; `/system/status` checks them. |
| D10 | **Dashboard session = bearer token in localStorage (foundation)** | Short-lived tokens; httpOnly cookies + refresh tokens with full RBAC in MVP 07. |
| D11 | **Console pages render after client session check** | Next.js instant-navigation validation set to `manual-warning` (see `next.config.ts`). |
| D12 | **Camera Manager lives in the edge agent, not the central server** (MVP 02) | Offline-first: capture and camera management must work without the server. The central `cameras` table becomes the synced registry in MVP 06. |
| D13 | **Dashboard talks to the agent directly in MVP 02** (`NEXT_PUBLIC_AGENT_URL`) | Single-site setup. Routing to many agents through the central server comes with device management (MVP 06). |
| D14 | **Agent verifies dashboard JWTs locally** (shared HS256 secret, `perms` claim) | No network call to the server, so management works while the server is offline. Per-device credentials in MVP 05–06. |
| D15 | **Preview = MJPEG with short-lived stream tokens** | Push-based, works in a plain `<img>`, no access token in URLs. Limited to ~4 simultaneous previews per browser (HTTP/1.1); multiplexed transport in MVP 07. |
| D16 | **Agent storage = stdlib sqlite3** | Light dependency footprint for Jetson / mini PCs; WAL for crash safety. |
| D17 | **Vision runs in the agent, one thread per camera, fed by a 2-frame drop-oldest queue** (MVP 03) | Capture never waits for analysis; failures stay per camera. |
| D18 | **Separate `events.db`** | High write volume, own retention; can be quarantined and rebuilt without touching camera config. |
| D19 | **Overlays drawn by the agent into MJPEG** | Exact frame/overlay alignment without a second real-time channel; per-viewer layer selection. |
| D20 | **Motion objects are never called people** | Without AI the engine can't classify; people counting is AI-only (MVP 04/08). |
| D21 | **AI runs on OpenCV DNN with an ONNX model** (MVP 04) | No PyTorch/ONNX Runtime dependency; same runtime as capture; works on Jetson and mini PCs. |
| D22 | **AI is a separate supervised layer** | One inference thread + watchdog; any AI failure is contained and the Standard Engine is the permanent fallback. |
| D23 | **Models are local files pinned by SHA-256** | Spec: no automatic downloads from unverified sources. |
| D24 | **Claude only for optional, rate-limited appearance attributes** | Per-frame detection must work offline; the cloud call is per track, non-blocking, privacy-constrained, opt-in. |
| D25 | **Sync queue is a table in `events.db`, written in the event's transaction** (MVP 05) | Power loss can't separate an event from its upload entry; no second store to reconcile. |
| D26 | **Device ID = random UUID created on first start** | Stable, unique per installation, not derived from hardware serials (privacy, board swaps). |
| D27 | **Agent-defined sync contract, server implements it in MVP 06** | Edge was built first; the contract (`docs/EDGE_AGENT.md`) is tested against an in-process fake server. |
| D28 | **Enrollment token once → per-device secret, encrypted with the agent's Fernet key** | Revocable per device; a leaked dashboard JWT secret doesn't let anyone upload as a device. |
| D29 | **HTTPS mandatory for the server link** (explicit lab-only override) | Event data and device credentials cross networks. |
| D30 | **Retention never deletes un-uploaded events; disk protection may (counted)** | Offline weeks must not lose data, but a full disk must not stop capture. |
| D31 | **Installed as a hardened systemd service under a dedicated user** | Restricted permissions, auto-restart, no root at runtime. Not installed into camera firmware. |
| D32 | **Device secrets and enrollment tokens stored as SHA-256 only** (MVP 06) | 256-bit random values: a hash is enough to verify and useless if the DB leaks. |
| D33 | **Rotation keeps the old secret valid until the new one is first used** | A lost rotation reply must never lock a device out; then a short grace period. |
| D34 | **Status derived on the server from heartbeats** (+ offline monitor) | One definition for all clients; devices only report facts. |
| D35 | **Configuration = immutable versions, optimistic concurrency (`base_version`), rollback = new version** | Audit trail, no lost updates between administrators, safe undo. |
| D36 | **Event Processor independent of HTTP, idempotent by UUID at the database** | Correct under retries and concurrency; can move to a worker behind a queue later. |
| D37 | **WebSocket auth by first message, in-process hub** | No tokens in URLs/logs; Redis pub/sub when the API scales to several workers (MVP 09). |
| D38 | **Cameras table = registry reported by devices** (`device_id` + agent camera id) | Central view of every camera without copying stream URLs or credentials off the edge. |

## Error format

```json
{ "error": { "code": "validation_error", "message": "Request validation failed",
             "details": [...], "request_id": "4f1c…" } }
```

## Roadmap

MVP 02 Camera Integration · 03 Standard Vision Engine · 04 AI Engine · 05 Edge Runtime ·
06 Central Server (devices, event ingestion, WebSocket) · 07 Web Dashboard · 08 Analytics & Alerts ·
09 Production Release.
