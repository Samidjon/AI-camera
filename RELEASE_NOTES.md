# AI Camera Intelligence 1.0.0 — release notes

**Date:** 2026-11-17 · **Status:** production release (MVP 01–09 integrated)

Offline-first video analytics: IP / USB cameras → edge agent next to the cameras (motion analysis
without AI, optional local AI) → central server → web control center (browser, installable on
phones). Video stays on the local network; only events leave the edge.

## What's in 1.0.0

| Area | Highlights |
|---|---|
| Cameras | RTSP, ONVIF discovery, USB, files, FFmpeg sources; private-network scan; encrypted credentials; automatic reconnection |
| Standard engine (no AI) | Motion episodes, tracking, zones (enter / exit / dwell), line crossing with direction, per-minute activity summary |
| AI engine (optional) | Local YOLOX person detection, ByteTrack temporary tracks, clothing colour attributes, optional Claude attribute provider; fail-safe fallback to the standard engine |
| Edge runtime | Local SQLite storage, bounded upload queue, exactly-once sync, backups with integrity checks, crash recovery, autostart (systemd / Windows task) |
| Central server | Device enrollment, credential rotation / revocation, versioned configuration with rollback, idempotent event ingestion, WebSocket live updates |
| Dashboard | Overview, live wall (1/4/9/16), cameras, devices, events (search, CSV), anonymous observations, AI engine, analytics, alerts, system health, users / roles / settings; dark & light; PWA |
| Analytics & alerts | Motion and people analytics kept separate, time-zone aware, CSV / PDF reports; 14 alert conditions, cooldown, in-app + e-mail |
| Production | Docker Compose with Caddy (automatic HTTPS), backup / restore / update-with-rollback / uninstall scripts, monitoring endpoint |

## Verified for this release

* **Unit / integration:** central server 105 passed (1 PostgreSQL-only test skipped without `TEST_DATABASE_URL`), edge agent 238 passed (2 skipped: real USB / RTSP camera tests that need `ACI_TEST_*` variables).
* **Browser end-to-end** (real server + agent + generated video + production build): 15 scenarios.
* **Offline scenarios A–E** against real processes: **29 / 29 checks passed** —
  [docs/TEST_REPORT.md](docs/TEST_REPORT.md#offline-scenarios).
* **Performance** 1 / 5 / 10 / 20 cameras, standard and AI mode, on a named machine —
  [docs/TEST_REPORT.md](docs/TEST_REPORT.md#performance). No capacity is claimed beyond what was measured.
* **AI-enhanced mode, real camera:** USB webcam → AI person detection → central server → dashboard
  (observations, people analytics) verified with live data.

## Acceptance criteria (MVP 09 §11)

| Criterion | Result |
|---|---|
| All MVPs integrated | ✔ one repository, one release, shared contracts |
| No critical defects | ✔ none open; issues found during MVP 09 fixed (login throttling, security headers, secure delete) |
| Main tests pass | ✔ see above |
| Cameras work without AI | ✔ scenario A, C |
| Cameras work without internet | ✔ scenario A (no non-loopback connection observed) |
| Edge agent works without central server | ✔ scenarios A, B, D |
| AI can be enabled and disabled | ✔ scenarios B, C (no camera restart) |
| Local synchronisation is reliable | ✔ scenario E (all events, no duplicates, nothing dropped), crash D |
| Dashboard shows real data | ✔ E2E + live webcam data |
| Documentation complete | ✔ README, ARCHITECTURE, INSTALLATION, EDGE_AGENT_GUIDE, CAMERA_INTEGRATION, API_DOCUMENTATION, USER_MANUAL, ADMIN_MANUAL, TROUBLESHOOTING, SECURITY, TEST_REPORT |

## Known limitations

* **Not measured on NVIDIA Jetson or a low-power mini PC** — the installers support them, but no
  capacity numbers exist for that hardware yet. Run `tests/system/performance.py` there before sizing.
* **Docker images were validated with `docker compose config`, not built in this environment**
  (building pulls base images). Build once on the target host; the update script rolls back on failure.
* **PostgreSQL:** automated tests run against SQLite; the PostgreSQL migration test runs when
  `TEST_DATABASE_URL` points to a disposable database. The schema has run on Supabase PostgreSQL
  during development.
* Rate limiting and the WebSocket hub are per API process — run one API worker (as the compose file
  does) until a shared Redis implementation is added.
* Edge-device reboot (scenario D) was verified as a hard process kill + restart; autostart at boot is
  provided by systemd / the Windows scheduled task, not exercised by an actual power cycle here.
* People counts are *observations* (temporary tracks per camera), not unique visitors — by design.

## Upgrade from 0.8.0

Server: `scripts/server/update.sh v1.0.0` (Docker) or `uv sync && alembic upgrade head` (native; no
new migration since 0.8.0). Agent: `aci-agentctl update` (Linux) / re-run `install.ps1` (Windows).
Optional new settings: `ACI_RATE_LOGIN_*` (sign-in throttling, sensible defaults).

## Security notice

The development Supabase database password was exposed in an early commit of the public
repository. It must be rotated by the project owner (Supabase → Database settings) before any
production use of that project. See [docs/SECURITY.md](docs/SECURITY.md).
