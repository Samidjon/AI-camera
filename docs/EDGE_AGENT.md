# Edge Agent Runtime (MVP 05, server link completed in MVP 06)

The edge agent (`edge/camera-agent`) is an **autonomous program on the edge computer next to the
cameras** — a Linux mini PC, an NVR-class box or an NVIDIA Jetson. It captures video, runs the
Standard Vision Engine and the optional AI Engine, stores events locally and — if a central server is
configured — synchronizes with it.

> The agent is **not** installed into camera firmware. Cameras are reached over the network
> (RTSP / ONVIF / HTTP streams) or USB. Installing third-party software on IP cameras is not supported.

## What works without what

| Without… | Behaviour |
|---|---|
| Central server (never configured) | Standalone mode: everything works, nothing is queued for upload |
| Central server (down / internet off) | Events are queued locally (bounded) and uploaded after recovery — each exactly once |
| AI / model / GPU | Standard Vision Engine continues (motion, lines, zones, events) |
| Dashboard | Agent keeps running; the dashboard is only a control surface |
| Device revoked by the server | Uploads stop, credential is deleted, **local operation continues** |

## Components

| Path | Responsibility |
|---|---|
| `agent/device.py` | Device identity (random UUID created on first start, stable across restarts and updates), static hardware / OS facts |
| `monitoring/hardware.py` | Live CPU, RAM, disk, temperature (sensors / Jetson thermal zones / GPU), GPU (Jetson load or `nvidia-smi`), network interfaces and throughput |
| `networking/client.py` | HTTPS client for the server's device API; error mapping (unavailable / revoked / rejected) |
| `networking/link.py` | `server_link` row: server URL, remote device ID, **encrypted** device credential, heartbeat / sync times, applied config version |
| `synchronization/manager.py` | Network Manager thread: registration, heartbeat, batched upload, config download + ack, backoff, revocation |
| `synchronization/backoff.py` | Exponential backoff with full jitter, `Retry-After` honoured |
| `events/store.py` | Event DB + **sync queue** (same transaction as the event), retention that never deletes un-uploaded events, bounded queue |
| `monitoring/system_log.py` | System log in SQLite (bounded, redacted, non-blocking) |
| `storage/backup.py` | Online backups (SQLite backup API) with integrity check, rotation, restore |
| `agent/scheduler.py` | Automatic daily backup |
| `api/device_api.py` | `/api/v1/device*` |
| `__main__.py` | CLI: `run`, `configure`, `status`, `backup`, `backups`, `restore`, `register`, `unlink`, `version` |
| `deploy/linux/` | `install.sh`, hardened `aci-agent.service`, `aci-agentctl` |

## Local data

`<data_dir>` (default `./data`, `/var/lib/aci-agent` when installed):

| File | Content |
|---|---|
| `agent.db` | cameras, groups, encrypted camera credentials, vision rules, AI config, **device**, **server_link**, **system_logs** |
| `events.db` | events + **sync_queue** |
| `secret.key` | Fernet key (owner-only). Encrypts camera passwords and the device credential |
| `backups/<UTC timestamp>/` | `agent.db`, `events.db` copies |

Both databases use WAL journaling, `synchronous=NORMAL`, explicit `BEGIN IMMEDIATE` transactions and
`PRAGMA user_version` migrations (existing installations are upgraded in place on start). An event and
its queue entry are written in **one transaction**, so a power cut can't leave an event that will never
be uploaded, or a queue entry without an event.

### Retention and limits

* Events older than `ACI_AGENT_EVENTS_RETENTION_DAYS` are deleted — **except events still waiting for
  upload**.
* Size limit (`EVENTS_MAX_DB_MB`) and free-disk floor (`EVENTS_MIN_FREE_DISK_MB`) prune the oldest
  events even if not uploaded (disk protection wins; counted as `sync.dropped`, never silent).
* The sync queue is bounded (`ACI_AGENT_SERVER_SYNC_QUEUE_MAX`, default 200 000); beyond it the oldest
  queue entries are dropped (the events stay in the local database) and counted.

## Central server link

### Registration

1. An administrator creates a one-time **enrollment token** on the central server (MVP 06).
2. The device registers once — Devices page → *Register with a central server*, or
   `aci-agentctl register --server https://server.example.com`, or
   `ACI_AGENT_SERVER_URL` + `ACI_AGENT_SERVER_ENROLLMENT_TOKEN` in the env file (auto-registration at start).
3. The server answers with the device's own credential; it is stored **encrypted** in `server_link`.
   From then on the enrollment token is not used.

### Transport security

* **HTTPS is required.** `http://` is refused unless `ACI_AGENT_SERVER_ALLOW_INSECURE_HTTP=true`
  (development / isolated lab only; the dashboard then shows "HTTP — insecure").
* Certificates are verified against the system CAs or `ACI_AGENT_SERVER_CA_FILE` (private CA).
  A verification failure is treated as "server unreachable" — never bypassed.
* Redirects are not followed (no credential leakage to another host). URLs with embedded credentials
  are rejected.

### Sync cycle and states

```
unconfigured ──register──► online ◄──────► offline (backoff 2 s … 5 min, jitter)
                              │
                              └── 401/403 ──► revoked (credential deleted; re-register to resume)
error: plain HTTP refused / enrollment token rejected / invalid registration response
```

* **Heartbeat** every 30 s (`ACI_AGENT_SERVER_HEARTBEAT_INTERVAL_S`; the server can change it only
  through a configuration push): identity, version, hardware info, live metrics, local health
  (database check, storage paused), camera status counts and a camera list (id, name, type, status,
  fps, resolution, AI flag, last error — never stream URLs or credentials), AI mode, vision and
  event-store stats, sync-queue stats, link state, applied config version. When the upload backlog is
  cleared an extra heartbeat is sent at once, so the server shows "online" instead of "synchronizing".
* **Upload**: batches of ≤200 events (oldest first). Unconfirmed events stay queued and are retried
  with the **same UUIDs**; the server's per-event answer makes retries idempotent. A batch the server
  rejects as a whole (413 / 422) is halved until the bad event is isolated and dropped (counted).
* **Configuration**: when the heartbeat reply carries a newer `config_version`, the agent downloads
  the document, verifies its SHA-256 checksum (mismatch → `failed`, nothing applied), applies known
  keys through the same code paths as the local API (`device_name`, `heartbeat_interval_s`,
  `ai_enabled`, `events`, `cameras` → enable / max_fps / AI / motion settings / zones), reports unknown
  keys as `ignored` and invalid values as `errors`, stores the version + document and acknowledges.
  In-memory settings (`events`, `heartbeat_interval_s`) are re-applied from the stored document after
  a restart.
* A link stored with `http://` (lab mode) stops working when `ALLOW_INSECURE_HTTP` is turned off — the agent
  goes to `error` instead of sending data in clear text.
* **Credential rotation**: when the heartbeat reply says `rotate_credentials`, the agent fetches a new
  secret, stores it encrypted, then uses it (the server keeps the old one valid until then).

### Server contract (implemented in MVP 06 — `docs/CENTRAL_SERVER.md`)

All bodies are JSON. Device calls carry `Authorization: Bearer <device secret>` and
`X-ACI-Device-ID: <device id>`.

| Call | Request | Response |
|---|---|---|
| `POST /api/v1/devices/register` | `Authorization: Bearer <enrollment token>`; `{device_id, device_name, agent_version, hardware}` | `201 {device_id, device_secret, heartbeat_interval_s}` |
| `POST /api/v1/devices/heartbeat` | `{device_id, device_name, agent_version, sent_at, config_version, heartbeat_interval_s, link_state, hardware, metrics, health, sync, cameras, camera_list, ai, vision, events, uptime_s}` | `200 {config_version, rotate_credentials, heartbeat_interval_s, server_time, status}` |
| `POST /api/v1/devices/credentials/rotate` | — | `200 {device_secret}` |
| `POST /api/v1/events/batch` | `{device_id, events: [{id, camera_id, event_type, source, occurred_at, zone_id, line_id, track_id, metadata, created_at}]}` | `200 {accepted: [id], duplicate: [id], rejected: [{id, reason}]}` |
| `GET /api/v1/devices/{id}/configuration` | — | `200 {version, config: {…}, checksum}` |
| `POST /api/v1/devices/{id}/configuration/ack` | `{version, status: applied\|partial\|failed, applied, ignored, errors}` | `200` |

Error semantics: 401 / 403 = revoked (stop, delete credential), 429 / 5xx / network = retry with
backoff (`Retry-After` honoured), other 4xx = request rejected.

**Status:** implemented on both sides and verified end-to-end (`tests/e2e/test_central_server.py`
starts the real server and syncs real agents over HTTP) and live on the development machine.

## Health events and live wall (MVP 07)

* `agent/health_events.py` records `camera_error` / `camera_recovered` (a camera that was online starts
  failing / streams again, with the failure kind) and `ai_error` / `ai_recovered` (enabled AI engine
  degraded / back to active) as local events (source `system`). They are queued and synchronized like
  all events; the server adds `device_offline` / `device_error` / `device_online`.
* A change of any camera status or of the AI mode triggers a heartbeat within ~5 s, so the dashboard
  doesn't wait for the 30 s cycle.
* `WS /api/v1/live` (`api/live_api.py`): one WebSocket carries every camera of a monitoring wall
  (≤16). Auth by first message (`cameras:read`), then `subscribe` with cameras, overlay layers,
  `max_fps` (1–15), `max_width` (160–1920). Binary frames: `uint32` header length + JSON header
  (`camera_id`, `seq`, `ts`, `width`, `height`) + JPEG; a JSON `status` message per second with
  each camera's status and measured FPS. Only the newest frame per camera is sent; encodes are cached
  per frame / size / overlay variant and shared between viewers.

## Command line

```bash
python -m aci_agent run                  # same as no argument
python -m aci_agent status               # JSON: identity, DB health, queue, link, latest backup
python -m aci_agent configure            # wizard → .env (or --output /etc/aci-agent/agent.env)
python -m aci_agent backup | backups | restore <id>
python -m aci_agent register --server https://server.example.com [--token-file token.txt]
python -m aci_agent unlink
```

## Backups

* Automatic every `ACI_AGENT_RUNTIME_BACKUP_INTERVAL_H` hours (default 24; first one 10 min after
  start), keeps `ACI_AGENT_RUNTIME_BACKUP_KEEP` (default 7). Manual: Devices page, `POST
  /api/v1/device/backups` or `aci-agentctl backup`. Safe while the agent runs.
* Every copy is checked with `PRAGMA integrity_check`.
* `secret.key` is **not** included — back it up separately (without it, camera passwords and the
  device credential in a backup can't be decrypted; that is intentional).
* Restore (agent stopped): `aci-agentctl restore <id>`. Current files are kept as `*.before-restore`.

## Security

| Requirement | Implementation |
|---|---|
| Device credentials | Per-device secret from the server, Fernet-encrypted at rest, deleted on revocation |
| TLS | HTTPS enforced, certificate verification, custom CA option, no redirects |
| Secure configuration | `/etc/aci-agent/agent.env` root-owned mode 600 (read by systemd), data dir mode 700, `UMask=0077` |
| Restricted permissions | Dedicated system user `aci-agent` (no shell), only `video` group; systemd sandbox (`ProtectSystem=strict`, `NoNewPrivileges`, empty capability set, restricted address families…) |
| No secrets in logs | Redaction for console and system log; secrets never returned by the API; CLI reads the enrollment token hidden or from a file; `aci-agentctl` exports env vars instead of passing them on a command line |
| Secure updates | `aci-agentctl update` requires a SHA-256 checksum, backs up data and code, health-checks and **rolls back automatically** on failure |
| Revocation | 401/403 → credential deleted, uploads stop, local operation continues |

## Agent API

| Endpoint | Permission | |
|---|---|---|
| `GET /api/v1/device` | `devices:read` | identity, hardware, metrics, sync status, storage, backups |
| `PATCH /api/v1/device` | `devices:manage` | rename `{name}` |
| `GET /api/v1/device/sync` / `POST /api/v1/device/sync` | read / manage | status / sync now |
| `POST /api/v1/device/link` / `DELETE /api/v1/device/link` | `devices:manage` | register `{server_url, enrollment_token}` / unlink |
| `GET /api/v1/device/logs?level=&component=&limit=` | `devices:read` | system log (newest first) |
| `GET /api/v1/device/backups` / `POST …` | read / manage | list / create |

`/api/v1/system/status` additionally reports `device` and `sync`.

## Configuration reference (new in MVP 05)

| Variable | Default | |
|---|---|---|
| `ACI_AGENT_DEVICE_NAME` | host name | initial device name |
| `ACI_AGENT_SERVER_URL` | — | central server (empty = standalone) |
| `ACI_AGENT_SERVER_ENROLLMENT_TOKEN` | — | one-time token for auto-registration |
| `ACI_AGENT_SERVER_ALLOW_INSECURE_HTTP` | `false` | lab only |
| `ACI_AGENT_SERVER_CA_FILE` | — | private CA bundle |
| `ACI_AGENT_SERVER_HEARTBEAT_INTERVAL_S` | 30 | 5–3600 |
| `ACI_AGENT_SERVER_SYNC_BATCH_SIZE` | 200 | 1–1000 |
| `ACI_AGENT_SERVER_BACKOFF_INITIAL_S` / `_MAX_S` | 2 / 300 | |
| `ACI_AGENT_SERVER_SYNC_QUEUE_MAX` | 200000 | |
| `ACI_AGENT_RUNTIME_SYSTEM_LOG_LEVEL` | INFO | stored in `system_logs` |
| `ACI_AGENT_RUNTIME_SYSTEM_LOG_MAX_ROWS` | 5000 | |
| `ACI_AGENT_RUNTIME_BACKUP_INTERVAL_H` | 24 | 0 = off |
| `ACI_AGENT_RUNTIME_BACKUP_KEEP` | 7 | |

## Tests

`tests/integration/test_edge_runtime.py`, `tests/integration/test_edge_runtime_api.py`,
`tests/unit/test_cli.py` (33 tests):

| Spec scenario | Test |
|---|---|
| Autonomy | `test_standalone_device_works_without_server`, `test_device_api_reports_identity_hardware_and_standalone_sync` |
| Internet off | `test_internet_off_backoff_grows_with_jitter`, `test_tls_failure_is_treated_as_unreachable` |
| Server off | `test_server_down_events_queue_then_sync_after_recovery`, `test_linked_agent_with_cameras_survives_outage_and_ai_failure` |
| Restart | `test_restart_keeps_queue_link_and_credentials`, `test_auto_registration_from_settings`, `test_device_identity_is_stable_and_unique` |
| Power loss | `test_power_loss_snapshot_is_consistent` (DB files copied mid-write, reopened, event ↔ queue consistency) |
| DB recovery | `test_corrupt_event_database_recovers_and_sync_continues`, `test_backup_and_restore` |
| Sync after recovery / dedupe | `test_lost_acknowledgement_does_not_duplicate`, `test_rejected_events_leave_queue_and_are_counted`, `test_sync_queue_is_bounded`, `test_retention_keeps_events_waiting_for_upload` |
| Multiple cameras | `test_linked_agent_with_cameras_survives_outage_and_ai_failure` (3 cameras), `test_revocation_through_api_keeps_cameras_running` |
| AI failure | same test: AI fails (no model) while offline — cameras and sync unaffected |
| Security | `test_plain_http_refused_unless_allowed`, `test_revoked_device_stops_sync_and_keeps_working`, `test_device_management_requires_permission`, `test_system_log_is_bounded_and_redacted`, `test_env_file_is_owner_only` |

**Not verified in this environment:** the Linux installer, systemd unit and `aci-agentctl` were
written for Ubuntu 22.04/24.04 and JetPack 6 and syntax-checked (`bash -n`), but development ran on
Windows without WSL — run the install on a real Linux / Jetson box before production. A real power cut
was simulated by snapshotting files, not by cutting power.
