# Central Server (MVP 06)

The central server (`apps/api-server`) joins many edge agents into one managed system: device
registry, health, event storage, versioned remote configuration and live updates for the dashboard.

> **Not required for local operation.** Every edge agent records, analyses and stores events on its
> own. When the server is down the agents queue events and upload them after recovery — each event
> exactly once (verified end-to-end, see Tests).

## Device lifecycle

```
admin: POST /devices/enrollment-tokens ──► token (shown once, stored as SHA-256)
agent: POST /devices/register  (Bearer <enrollment token>) ──► device secret (shown once, stored as SHA-256)
agent: POST /devices/heartbeat (Bearer <device secret> + X-ACI-Device-ID) every 30 s
agent: POST /events/batch  · GET /devices/{id}/configuration · POST …/configuration/ack
admin: PATCH /devices/{id} · …/configuration · POST …/revoke · …/rotate-credentials · DELETE /devices/{id}
```

* **Registration** — enrollment tokens have an expiry (default 24 h) and a use count (default 1). A
  device that registers again (re-install, or after revocation with a new token) keeps its ID; all its
  previous credentials are revoked.
* **Authentication** — per-device secret (256-bit random), checked by hash; the `X-ACI-Device-ID`
  header must match the credential's device. A device can only touch its own resources (403 otherwise).
* **Credential rotation** — automatic after `ACI_DEVICE_CREDENTIAL_MAX_AGE_DAYS` (90) or on request
  (dashboard → Rotate credential). The heartbeat reply says `rotate_credentials: true`; the device
  calls `POST /devices/credentials/rotate`, stores the new secret (encrypted) and uses it. The old
  secret stays valid **until the new one is first used**, then for a grace period
  (`ACI_DEVICE_CREDENTIAL_GRACE_S`, 10 min) — a lost reply can't lock a device out.
* **Revocation** — all credentials revoked; the device gets 401 `device_revoked`, stops syncing and
  keeps working locally. **Deletion** revokes and removes the device; its events stay (device link set
  to NULL, `camera_ref` kept).
* **Groups** — `device_groups`; an enrollment token can put new devices into a group.

## Status

Derived on the server from the latest heartbeat:

| Status | When |
|---|---|
| `pending` | registered, no heartbeat yet |
| `online` | heartbeat on time, nothing wrong |
| `synchronizing` | events waiting in the device's upload queue |
| `degraded` | cameras failing, AI degraded/error, event storage paused, disk ≥ 95 %, temperature ≥ 85 °C, last configuration failed |
| `error` | the device reports a failed local database check |
| `offline` | no heartbeat for max(90 s, 3 × the device's interval) — set by a background monitor (every 10 s) with a notification |
| `revoked` | revoked by an administrator |

AI status (`enabled`, `mode`, `reason`) and camera status (totals by status plus one row per camera
in `cameras`, synced from every heartbeat — no stream URLs or credentials leave the device) are
shown separately.

## Event Processor

`app/processing/event_processor.py` — independent of the HTTP layer (takes a DB session, the
authenticated device and raw events), so it can later run as a separate worker behind a queue.

1. **Structure** — each event validated on its own (UUID, known event type, timezone-aware time not
   more than 5 min in the future nor older than 400 days, ≤ 16 KB metadata). Invalid events are
   `rejected` with a reason; the rest of the batch is stored.
2. **Device rights** — authenticated device only; an event UUID already stored for another device is
   rejected.
3. **Idempotency** — `INSERT … ON CONFLICT (id) DO NOTHING RETURNING id`; repeats (lost
   acknowledgements, concurrent retries) are reported as `duplicate` and stored once.
4. **Aggregates** — hourly counts per device / camera / event type (`event_statistics`), incremented
   in the same transaction for newly stored events only.
5. **Transaction** — the batch, statistics and a `synchronization_logs` row commit together; only then
   is the batch announced over WebSocket. A database error rolls everything back (503) and the agent
   retries the same UUIDs later — nothing is lost or doubled.

## Configuration

Versioned per device (`device_configurations`, immutable rows).

| Key | Effect on the agent |
|---|---|
| `device_name` | rename |
| `heartbeat_interval_s` | 5–3600 |
| `ai_enabled` | global AI switch |
| `events.retention_days`, `events.max_db_mb`, `events.min_free_disk_mb` | local retention / limits |
| `cameras.<id>.is_enabled`, `.max_fps`, `.ai_enabled` | per-camera settings |
| `cameras.<id>.motion.{enabled, algorithm, sensitivity, min_object_area, process_fps, motion_end_delay_s, restrict_to_zones, activity_interval_s}` | Standard Vision Engine |
| `cameras.<id>.zones[]` (`name`, `role`, `polygon` 0..1, `is_enabled`) | replaces the camera's detection zones |

* `PATCH /devices/{id}/configuration` with `base_version` (optimistic concurrency → **409
  `configuration_conflict`** when someone changed it meanwhile) and a JSON merge patch (`null` removes
  a key) or `replace: true`. Unknown keys / invalid values → 400 `invalid_configuration`.
* The heartbeat reply carries the desired version; the agent downloads the document with a SHA-256
  checksum, verifies it, applies key by key through its normal code paths, persists it and
  acknowledges `applied` / `partial` / `failed` with `applied`, `ignored`, `errors`.
* `POST …/configuration/rollback {to_version, base_version}` creates a new version with the old
  content; history is never rewritten. A newer version marks unacknowledged older ones `superseded`.

## Real-time updates (WebSocket)

`/api/v1/ws` — authenticate with the first message `{"type":"auth","token":"<access token>"}` (no
token in URLs). Messages `{type, data, at}`:

| Type | Permission | Content |
|---|---|---|
| `device.status` | devices:read | status, reason, heartbeat, AI, cameras, queue, config versions |
| `camera.status` | cameras:read | a camera changed status (or was reported for the first time) |
| `event.created` | events:read | stored batch summary + up to 20 events |
| `sync.status` | devices:read | upload / configuration acknowledgement result |
| `system.notification` | devices:read | registered, offline, revoked, configuration queued / failed … |

Per-client bounded queues (slow clients lose oldest messages, never block the server); server ping
every 25 s. Single process: with several API workers each serves its own clients (shared bus with
Redis planned for horizontal scaling, MVP 09).

## Reliability

| Requirement | Implementation |
|---|---|
| Idempotent processing | event UUID primary key + `ON CONFLICT DO NOTHING`; statistics only for new rows |
| Retry mechanism | agent: exponential backoff with jitter, honours `Retry-After`; server returns 429 / 5xx for retryable conditions |
| Rate limiting | token buckets per device (events 10/s burst 40, heartbeats 1/s burst 5, rotation) and per IP (registration 10/min); 429 + `Retry-After` |
| Transactions | one transaction per batch / configuration change / acknowledgement |
| Error recovery | rollback on failure; monitor survives DB outages; agents keep queueing |
| Structured logging | JSON logs with request ID, device ID; no secrets |
| Health monitoring | `/system/status`: DB, Redis, devices by status, event-processor counters, WebSocket clients, rate-limit rejections |
| Local events kept until confirmed | the agent removes an event from its queue only on `accepted` / `duplicate` |

## Security

Device-specific credentials (hashed, rotatable, revocable) · TLS (agents require HTTPS; terminate TLS
in front of uvicorn — reverse proxy or `--ssl-certfile`) · role-based access (`devices:read`,
`devices:manage`, `events:read`; operators/viewers read only) · audit log for registration,
re-registration, rotation, configuration changes / acknowledgements / rollbacks, revocation,
deletion, group changes · configuration delivered only to the authenticated device, checksum-verified,
allow-listed keys only.

## Database (migration 0002)

| Table | Purpose |
|---|---|
| `edge_devices` | device registry: status, health snapshot, AI / camera / sync status, versions |
| `device_credentials` | hashed device secrets, origin (enrollment / rotation), expiry, revocation |
| `device_enrollment_tokens` | hashed enrollment tokens, expiry, uses |
| `device_groups` | grouping |
| `device_heartbeats` | heartbeat history (pruned after `ACI_HEARTBEAT_RETENTION_DAYS`, 7) |
| `synchronization_logs` | every upload / configuration ack / registration / rotation |
| `device_configurations` | immutable configuration versions with acknowledgement |
| `event_statistics` | hourly aggregates |
| `cameras` (+ `device_id`, `external_id`, `ai_enabled`, `last_error`) | camera registry reported by devices |
| `camera_events` (+ `device_id`, `camera_ref`, `line_id`) | events |

RLS is enabled on every new table (Supabase), as for MVP 01 tables.

## Tests

| Spec scenario | Where |
|---|---|
| Several devices connect | `test_register_heartbeat_and_list`, `test_concurrent_uploads_from_several_devices`, e2e (3 real agents) |
| Concurrent event uploads | `test_concurrent_uploads_from_several_devices` (4 devices × 2 parallel copies of every batch), e2e (3 threads) |
| Same event sent again | `test_event_batch_idempotent_and_statistics`, e2e (re-upload of 50 stored events) |
| Connection loss / recovery | `test_status_derivation_and_offline_monitor`, e2e (server stopped, agent queues, server restarted, backlog uploaded once) |
| Configuration version conflict | `test_configuration_versions_ack_conflict_and_rollback`, e2e |
| One device disabled | `test_revocation_and_deletion`, e2e (one agent revoked, others continue) |
| Server unavailable | e2e + agent tests (`test_server_down_events_queue_then_sync_after_recovery`) |

* Server: `apps/api-server/tests/integration/test_devices.py` (18 tests).
* End-to-end: `edge/camera-agent/tests/e2e/test_central_server.py` starts the real server (uvicorn,
  SQLite) and drives real agent network managers over HTTP.
* Also verified live on the development machine: the running agent (USB webcam + video file, AI on)
  registered from the dashboard, uploaded its real events, applied a configuration pushed from the
  dashboard (motion sensitivity, retention) and acknowledged it; with the server stopped the agent kept
  recording and queued, after restart the backlog arrived without duplicates.

**Not verified here:** PostgreSQL/Supabase run of migration 0002 against the real project (offline
DDL rendering is tested; the live Supabase project has no admin user yet), TLS termination in front
of the server, multiple API worker processes.
