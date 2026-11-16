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

## Error format

```json
{ "error": { "code": "validation_error", "message": "Request validation failed",
             "details": [...], "request_id": "4f1c…" } }
```

## Roadmap

MVP 02 Camera Integration · 03 Standard Vision Engine · 04 AI Engine · 05 Edge Runtime ·
06 Central Server (devices, event ingestion, WebSocket) · 07 Web Dashboard · 08 Analytics & Alerts ·
09 Production Release.
