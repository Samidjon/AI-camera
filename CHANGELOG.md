# Changelog

All notable changes are documented here. Format: [Keep a Changelog](https://keepachangelog.com/),
versioning: [SemVer](https://semver.org/).

## [1.0.0] — MVP 09 Production Release — 2026-11-16

### Added
- **Production deployment**: `docker-compose.yml` (PostgreSQL 16, Redis 7, API, dashboard, Caddy with
  automatic HTTPS as the only published service), hardened containers (non-root, read-only API,
  dropped capabilities, log rotation), migrations on start, `.env.production.example`.
- **Operations scripts** (`scripts/server/`): verified PostgreSQL backups with rotation and checksum,
  restore with safety dump, update with automatic code/image rollback, uninstall (data kept unless
  `--purge`).
- **Windows edge agent installer**: scheduled task as SYSTEM at start-up with restart on failure,
  restricted data folder; uninstall script.
- **System monitoring**: `GET /api/v1/system/monitoring` (backend, database, Redis, server disk, edge
  devices, cameras, AI, synchronization) and a **System health** page in the dashboard.
- **System tests against real processes** (`tests/system/`): offline scenarios A–E (29 checks) and
  performance measurement for 1 / 5 / 10 / 20 cameras in standard and AI mode. Report:
  `docs/TEST_REPORT.md`.
- Documentation set: `USER_MANUAL.md`, `ADMIN_MANUAL.md`, `TROUBLESHOOTING.md`, `SECURITY.md`,
  `TEST_REPORT.md`, production sections in `INSTALLATION.md`; `EDGE_AGENT.md` →
  `EDGE_AGENT_GUIDE.md`, `API.md` → `API_DOCUMENTATION.md`. `RELEASE_NOTES.md`.

### Security
- Sign-in brute-force protection: token buckets per client IP and per account (429 + Retry-After).
- Security headers on server, agent and dashboard (nosniff, frame denial, no-referrer,
  Permissions-Policy, CSP, `Cache-Control: no-store` for API, HSTS over HTTPS).
- CORS no longer allows credentials (bearer tokens only — no CSRF surface).
- Agent SQLite databases use `secure_delete` (deleted data overwritten on disk).
- Tests for tampered / `alg=none` tokens, SQL metacharacters, script injection, oversized input.

### Changed
- ARCHITECTURE: decisions D46–D51 (analytics honesty, alert transactions, Compose + Caddy, no-cookie
  auth, OS-service autostart, measured capacity only).

## [0.8.0] — MVP 08 Analytics, Reports & Alerts — 2026-11-16

### Added
- **Analytics engine** on the central server, computed only from stored events:
  - standard (motion) analytics: motion events, activity time, frequency per hour, hourly / daily
    series, weekday × hour heatmap, camera comparison, zone entries / exits / occupancy estimate /
    average dwell, line crossings by direction;
  - AI (people) analytics only when person events exist: observations (one per temporary track per
    camera), inbound / outbound from line direction, average time in view, time-of-day distribution,
    peak hours, per-camera comparison;
  - device health and AI performance (FPS, latency) from heartbeats.
  - Time zone aware (IANA, DST-correct buckets); motion counts are never shown as people counts;
    people analytics hidden without `observations:read`.
- **Reports** — daily, weekly, monthly, camera, device, zone, AI performance — as JSON, CSV and PDF
  (fpdf2, Unicode font).
- **Alert engine**: user-defined rules for 14 conditions (camera offline / reconnected, stream error,
  AI offline / recovered, model error, device offline, high CPU / memory / temperature, low disk,
  motion, zone activity, line crossed), scope per device / camera / zone / line, severity, cooldown
  per scope, automatic resolve on reconnect / recovery. Alerting never breaks event ingestion.
- **Notifications**: in-app (live `alert.created` over WebSocket, top-bar bell with open count) and
  e-mail via SMTP (background worker, 3 attempts, delivery log; skipped and recorded when SMTP is not
  configured), test e-mail endpoint. Migration 0004: `alert_rules`, `alerts`,
  `notification_deliveries`.
- Dashboard: **Analytics** page (charts with hover and table view, validated colour palette, report
  downloads), **Alerts** page (list, acknowledge / resolve, delivery details, rules editor,
  recommended health rules, SMTP status and test).
- Heartbeats now carry AI inference FPS / latency; stored per heartbeat for reports.
- Tests: 26 server tests (statistics, duplicates, time zones / DST, empty data, permissions, reports,
  rules, firing, cooldown, scope, resolve, metrics, device offline, e-mail delivery and retries,
  live publish, failure isolation); Playwright spec for analytics and alerts.

### Fixed
- `usePermissions().can` was a new function on every render, re-running effects that depend on it.

### Deployment (between 0.7.0 and 0.8.0)
- Dashboard deploys to Vercel (`apps/dashboard`), server/agent addresses configurable on the login
  screen, installable PWA for phones, CORS for `*.vercel.app` previews. See `docs/DEPLOYMENT.md`.

## [0.7.0] — MVP 07 Web Dashboard — 2026-11-16

### Added
- **Overview** with server-computed counters: cameras total / online / offline, edge devices total /
  active, motion events today and people observations today (separate), AI active devices, devices
  without AI, recent events, system health; live refresh over WebSocket.
- **Live monitoring** page: 1 / 4 / 9 / 16 grid, camera selection, single view, fullscreen, motion /
  detection / tracking / zone overlays, displayed and camera FPS, stream status. New agent endpoint
  `WS /api/v1/live` multiplexes all cameras of a wall over one socket (newest frame only, per-viewer
  size and rate).
- **Events**: fleet-wide view on the central server with categories (motion, person, zone, line,
  camera / AI / device errors), date range, camera filter, search, pagination, server-side CSV export;
  local agent tab kept. Camera / AI health transitions become events on the agent, device offline /
  error / back online on the server.
- **Anonymous observations** page and API (one temporary track on one camera, duration, direction,
  clothing attributes with confidence, detection source; never merged across cameras).
- **Settings**: users (create, role, enable / disable, password reset, delete; last-administrator
  protection), roles & permissions matrix, data retention and privacy (central event / observation
  retention applied hourly, drop or hide clothing attributes), fleet-wide camera / device / AI / event
  settings via versioned configuration, read-only system configuration.
- Permission catalog with `events:export`, `observations:read`, `users:manage`, `settings:manage`
  (migration 0003: `system_settings`, operator gets export + observations; viewers don't see
  observations by default). Navigation hides modules the role can't use.
- Device list shows CPU, RAM, GPU, temperature, disk, network; agents send an early heartbeat when a
  camera or AI status changes.
- Light theme matching the dark palette, theme switch (dark / light / system) without flash.
- One shared WebSocket per tab, top-bar Live / Reconnecting indicator, "data may be stale" banner;
  fleet statuses become "Unknown — last known" while the server is unreachable.
- **Playwright** end-to-end tests against the real stack (server, agent with generated video,
  production build): login, navigation, cameras, devices, live monitoring, AI, events and filters,
  permissions, WebSocket reconnection / server outage. Docs: `docs/DASHBOARD.md`.

### Fixed
- Local events page: filtering "People (AI)" failed (agent rejected AI event types) — since MVP 04.
- Live stream hook: a late close of a previous socket detached the current one (no frames in dev).
- Retention delete / heartbeat pruning could fail on SQLite with objects in the session.

## [0.6.0] — MVP 06 Central Server — 2026-11-16

### Added
- **Device management** on the central server: enrollment tokens (hashed, expiring, N uses),
  registration, device authentication (hashed per-device secrets + `X-ACI-Device-ID`), list / details /
  rename / groups, revocation, deletion (events kept), credential rotation (automatic after 90 days or
  on request; old secret valid until the new one is used + grace).
- **Device status** derived from heartbeats: online / synchronizing / degraded / error / offline /
  revoked / pending, with reason; background offline monitor; AI and camera status shown separately;
  camera registry synced from heartbeats (no stream URLs / credentials leave the edge); heartbeat history.
- **Event Processor** (independent module): per-event validation, device rights, idempotent storage by
  UUID (`ON CONFLICT DO NOTHING`), hourly aggregates, sync log; events API with filters and cursor
  pagination, statistics endpoints.
- **Versioned device configuration**: merge-patch edits with `base_version` conflict detection (409),
  validation of camera / motion / zone / AI / event / retention settings, checksum-verified delivery,
  acknowledgement (applied / partial / failed), rollback as a new version, history.
- **WebSocket** `/api/v1/ws` (first-message auth): device status, camera status, new events,
  synchronization status, system notifications; permission-filtered, bounded per-client queues.
- Rate limiting (per device / IP, `Retry-After`), audit log entries for all device actions,
  `/system/status` reports devices, event-processor counters and real-time stats.
- Migration 0002 (8 new tables, `cameras` / `camera_events` linked to devices, RLS).
- Edge agent: heartbeat endpoint per spec, camera list + local health in heartbeats, credential
  rotation, configuration checksum verification, appliers for cameras / motion / zones / events,
  configuration document persisted and re-applied after restart, immediate heartbeat when the upload
  backlog clears.
- Dashboard **Devices → Fleet**: live device table, add-device dialog (enrollment token), device panel
  (health, cameras, configuration editor with versions / rollback, sync log, rotate / revoke / delete),
  live notifications.
- Tests: 18 server integration tests, 4 new agent tests, an **end-to-end test** that starts the real
  server and syncs real agents over HTTP. Docs: `docs/CENTRAL_SERVER.md`.

### Fixed
- Device API returned naive timestamps on SQLite (browser showed heartbeats hours off); all device
  schemas now serialize UTC with an offset.
- The agent took the server's suggested heartbeat interval at registration, overriding its own
  configuration; the interval now changes only through a configuration push.

### Not verified
- Migration 0002 on the live Supabase project (PostgreSQL DDL rendering is tested offline).
- TLS termination and several API workers (WebSocket hub and rate limiter are per process).

## [0.5.0] — MVP 05 Edge Runtime — 2026-11-16

### Added
- **Device identity**: random UUID on first start (stable across restarts / updates), name, version,
  hardware and OS facts (CPU model, cores, RAM, board model, Jetson detection, OpenCV CUDA).
- **Hardware monitoring**: CPU, RAM, disk, temperature (sensors, Jetson thermal zones, or GPU), GPU
  (Jetson load / `nvidia-smi`), network interfaces and throughput, uptime.
- **Synchronization queue** in `events.db`, written in the same transaction as each event; bounded,
  counted drops; retention never deletes events still waiting for upload.
- **Network Manager**: registration with a one-time enrollment token → per-device secret stored
  encrypted; heartbeat; batched, idempotent event upload (accepted / duplicate / rejected); versioned
  configuration download, apply and acknowledgement; exponential backoff with jitter and `Retry-After`;
  states unconfigured / registering / online / offline / revoked / error; revocation deletes the
  credential and keeps local operation. HTTPS enforced (lab-only override), custom CA, no redirects.
- **System log** in SQLite (bounded, redacted, non-blocking) and **backups** (SQLite online backup,
  integrity-checked, rotated, automatic every 24 h, restore with undo copy).
- **CLI**: `run`, `configure` (wizard, env file mode 600), `status`, `backup`, `backups`, `restore`,
  `register`, `unlink`, `version`.
- **Linux / Jetson deployment**: `install.sh` (system user, `/opt`, `/etc` mode 600, `/var/lib` mode 700,
  locked dependencies), hardened `aci-agent.service`, `aci-agentctl` (start / stop / restart / status /
  logs / update with SHA-256 verification, backup and automatic rollback / uninstall [--purge]),
  `deploy/package.sh`. Docs: `docs/EDGE_AGENT_GUIDE.md`, `docs/JETSON.md`.
- Agent API `/api/v1/device*` (`devices:read` / `devices:manage`); `/system/status` reports device and sync.
- Dashboard **Devices** page: metrics, device facts, central-server link (register / sync / unlink),
  network, storage and backups, system log.
- 33 new agent tests (fake central server implementing the MVP 06 contract).

### Not verified
- Linux installer / systemd unit not run on real Linux or Jetson hardware yet (syntax-checked only).
- Real central-server endpoints arrive in MVP 06; until then a linked agent stays `offline` and queues.

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
