# Changelog

All notable changes are documented here. Format: [Keep a Changelog](https://keepachangelog.com/),
versioning: [SemVer](https://semver.org/).

## [0.4.0] — MVP 04 AI Engine — 2026-11-16

### Added
- Optional **AI Engine** in the edge agent (off by default, never required): `AIProvider` interface
  (initialize / load_model / predict / health_check / shutdown) with a YOLOX-S (COCO) person detector on
  OpenCV DNN — no PyTorch / ONNX Runtime dependency.
- Model registry with pinned SHA-256, versions, statuses and validation; local files only, no auto-download.
- ByteTrack person tracking (Kalman + two-stage association) with temporary camera-scoped IDs, entry /
  exit, direction, lost-track handling; AI events `person_detected`, `person_left`, `person_crossed_line`.
- Appearance attributes with value + confidence + provenance: on-device clothing colours; optional
  Claude provider (`claude-opus-5-5`, structured output, refusal fallback) for clothing type, bag,
  backpack, headwear — rate-limited, non-blocking, privacy-constrained, opt-in.
- AI modes (disabled, loading, active, degraded, offline, error, recovering), inference watchdog and
  timeouts, exponential-backoff recovery, memory guard, GPU probe and CPU fallback, status history,
  error log; Standard Engine always continues.
- Agent API `/api/v1/ai/*` (`ai:manage` for control); overlay layer `people`.
- Dashboard **AI Engine** page (status, model, inference FPS, latency, detections, memory, GPU, enable /
  disable, settings, Standard vs AI capabilities, errors); AI person events on the Events page.
- Optional dependency group `claude` (`uv sync --extra claude`). 42 new agent tests.

- Per-camera AI switch (`options.ai_enabled`) and an AI status indicator in the dashboard top bar.
- **Network discovery** in the edge agent: ONVIF WS-Discovery, RTSP service scan with unauthenticated
  `OPTIONS`, camera web-interface fingerprinting; private networks only, bounded, no credential
  guessing. API `/api/v1/discovery/*`, dashboard "Scan network" dialog with one-click add.

### Fixed
- Editing any field of a USB or file camera reconnected its stream (it was treated as a credential
  change) — a bug since MVP 02.
- Log lines with non-ASCII characters were dropped on Windows (legacy console code page); agent and
  server now log UTF-8.
- Clothing-colour estimation could report a face's skin tone as a clothing colour on head-and-shoulder
  views; colours now require a full-body box and exclude skin-tone pixels.
- AI person tracks stayed open when a camera stopped delivering frames while reconnecting.

## [0.3.0] — MVP 03 Standard Vision Engine — 2026-11-16

### Added
- **Standard Vision Engine** in the edge agent — AI-free, fully local: motion detection with four
  OpenCV algorithms (frame differencing, running-average background subtraction, MOG2, KNN),
  sensitivity, minimum object area, noise reduction (blur + morphology), fragment merging,
  lighting-change detection, empty-frame handling, exclude / monitor detection zones.
- Motion objects (never labelled as people) with appearance/disappearance, size, heading, duration;
  temporary camera-scoped Motion Track IDs (centroid + IoU distance-gated association).
- Line crossing (direction, per-session and per-day counters, jitter protection) and polygon zone
  monitoring (enter / exit with dwell time, occupancy estimate).
- Events: motion_started, motion_ended, object_entered_zone, object_exited_zone, line_crossed,
  camera_motion_activity — device-generated UUIDs, stored locally.
- Local event database (`events.db`): non-blocking batch writer, retention, size and free-disk
  limits, write pause on critical disk, integrity check with quarantine + recreate, safe shutdown.
- Local configuration storage: per-camera vision settings, zones, lines (`agent.db` migration v2).
- Resource management: per-camera analysis threads, 2-frame drop-oldest queues, configurable
  analysis FPS (frame skipping), CPU/RSS monitoring, memory-limit pause, graceful shutdown.
- Agent API: vision config/state, zones, lines, events (filters + keyset pagination), summary;
  overlay layers in MJPEG preview / snapshots.
- Dashboard: overlay toggles, live motion-detection status, zone & line editor on a camera
  snapshot, detection settings; **Events** page with filters and pagination; Overview shows today's
  motion events.
- 59 new agent tests (141 total). `docs/VISION_ENGINE.md`.

### Fixed
- Line crossings could be counted twice when a track point landed exactly on the line.
- Zone editor lost points on rapid clicks.

## [0.2.0] — MVP 02 Camera Integration — 2026-11-16

### Added
- **Edge Camera Agent** (`edge/camera-agent`, Python 3.12, standalone, offline-first) with its own
  SQLite database (WAL, versioned migrations) and local management API (FastAPI, port 8100).
- Camera Manager: add / edit / remove, connect / disconnect, test connection, camera groups,
  automatic reconnection with exponential backoff + jitter, restore on agent restart.
- Sources: RTSP, ONVIF (stream URI discovery with WS-Security digest), USB (DirectShow / V4L2),
  video files (real-time pacing, loop), any FFmpeg stream URL. OpenCV + FFmpeg backend with
  open/read timeouts, RTSP over TCP.
- One worker thread per camera; bounded frame buffers (latest-frame preview buffer, drop-oldest
  analysis queues); Connection Monitor with measured FPS and stall detection.
- Statuses online / offline / connecting / reconnecting / error / disabled based on real frame flow,
  with failure kinds (unreachable, auth_failed, not_found, unsupported_format, stream_lost, ...).
  RTSP DESCRIBE probe (Basic/Digest) to diagnose wrong password vs. wrong path vs. host down.
- Encrypted camera credentials (Fernet) in a separate table; credentials typed into URLs are moved
  to the secret store; never returned by the API or written to logs.
- Live preview over MJPEG with short-lived camera-scoped stream tokens; snapshots.
- Dashboard **Cameras** page: grid and list, live preview, details panel, add / edit dialogs with
  per-source fields and connection test, delete confirmation, groups, status / FPS / resolution,
  permission-aware controls. Overview shows real camera counts from the agent.
- Central server: access tokens now carry the role's `perms` so edge agents authorize locally.
- 82 agent tests (failure matrix, API, security, real OpenCV decoding, optional hardware/RTSP).
- `docs/CAMERA_INTEGRATION.md`.

## [0.1.1] — MVP 01 fixes found on Supabase — 2026-11-16

### Fixed
- Migration `0001` failed on PostgreSQL: the role seed bound `permissions` as VARCHAR into a JSONB
  column (SQLite accepted it). Now bound as JSON online and as a string literal in `--sql` mode.
  Verified upgrade → downgrade → upgrade on Supabase (PostgreSQL 17).
- Database URLs from Supabase with `?pgbouncer=true` (Prisma/ORM tab) were rejected by psycopg;
  ORM-only query parameters are now stripped.
- Raw passwords containing `/ ? # @ [ ]` broke URL parsing; the password part is now
  percent-encoded once, automatically.

## [0.1.0] — MVP 01 Core Foundation — 2026-11-16

### Added
- Monorepo structure: `apps/`, `edge/`, `services/`, `shared/`, `infrastructure/`, `docs/`, `scripts/`, `tests/`.
- **API server** (FastAPI, Python 3.12): API versioning (`/api/v1`), environment configuration,
  structured JSON logging with request IDs and credential redaction, centralized error envelope,
  request validation, OpenAPI docs (`/docs`, `/redoc`).
- Endpoints: `GET /api/v1/health`, `GET /api/v1/system/status`, `GET /api/v1/system/version`,
  `POST /api/v1/auth/login`, `GET /api/v1/auth/me`.
- Auth foundation: bcrypt password hashing, JWT access tokens, roles with permissions
  (administrator / operator / viewer), `require_permission` guard, login audit trail.
- **Database**: Alembic migration `0001` with `users`, `roles`, `cameras`, `camera_groups`,
  `camera_events`, `system_logs`, `audit_logs` — indexes, FKs, CHECK constraints, seeded roles,
  Row Level Security for Supabase.
- Admin CLI: `create-user`, `check-db`.
- **Dashboard** (Next.js 16, Tailwind 4, shadcn/ui, Lucide): dark surveillance control-center
  design system, sidebar + top bar shell, login, Overview (live server health/version/uptime),
  Cameras / Devices / Events / Analytics (explicitly marked as not implemented), Settings
  (account, connection, permissions). Stale-data and server-unreachable states.
- Shared contracts: `shared/contracts/openapi.json`, `shared/schemas/camera-event.schema.json`.
- Tests: backend unit, API integration, permission guard, migration tests (SQLite cycle, model/migration
  drift, PostgreSQL DDL generation, optional live PostgreSQL); dashboard lint, typecheck, build.
- `scripts/check.ps1` / `scripts/check.sh`.

### Changed (vs. original MVP 01 spec)
- No Docker / `docker-compose.yml`: PostgreSQL is hosted on Supabase; Redis is optional.
