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

## Modules (MVP 01)

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

## Error format

```json
{ "error": { "code": "validation_error", "message": "Request validation failed",
             "details": [...], "request_id": "4f1c…" } }
```

## Roadmap

MVP 02 Camera Integration · 03 Standard Vision Engine · 04 AI Engine · 05 Edge Runtime ·
06 Central Server (devices, event ingestion, WebSocket) · 07 Web Dashboard · 08 Analytics & Alerts ·
09 Production Release.
