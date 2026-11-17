# Administrator manual

Day-to-day administration of an installation. Installing it: [INSTALLATION.md](INSTALLATION.md).
Security hardening: [SECURITY.md](SECURITY.md). Problems: [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

## 1. First steps after installation

1. Sign in with the administrator created during installation and **change its password**
   (Settings → Users).
2. Create personal accounts for every person (no shared logins): Settings → Users → *Add user*.
   Roles: `viewer` (read-only, no people observations), `operator` (+ control, export, acknowledge),
   `administrator` (everything). Adjust operator / viewer permissions in Settings → Roles.
3. Settings → Data & privacy: event retention, observation retention, whether clothing attributes are
   stored / shown.
4. Alerts → Rules → **Add recommended health rules** (camera offline, AI offline, device offline,
   CPU / memory / temperature / disk). Add e-mail recipients if SMTP is configured.

## 2. Edge devices (agents)

* **Add**: Devices → *Add device* creates a one-time enrollment token (valid 24 h). Put it into the
  agent's configuration (`ACI_AGENT_SERVER_ENROLLMENT_TOKEN`) or run `aci-agentctl register`. The
  device appears as *online* after its first heartbeat.
* **Configuration push**: Settings → Cameras, devices, AI changes fleet-wide settings; each device
  gets a versioned configuration, acknowledges it, and you can roll back (Devices → device → History).
* **Credential rotation**: Devices → device → *Rotate credentials* (the agent picks up the new secret
  on its next heartbeat). **Revoke** blocks a stolen / retired device immediately.
* A device that is *offline* keeps working locally (motion, AI, event storage). Its events arrive
  when it reconnects — nothing to do.

## 3. Cameras

Cameras are added on the edge agent that can reach them (Cameras → *Add* or *Scan network*;
private networks only). RTSP / ONVIF credentials are stored encrypted on the agent and never sent
to the server. Zones and lines are drawn per camera (Cameras → camera → Vision).

## 4. AI engine

AI Engine page: enable / disable, model, inference FPS and latency, errors. AI is optional: when it is
off or fails, cameras and motion analytics continue unchanged (tested: scenarios B and C in
[TEST_REPORT.md](TEST_REPORT.md)). Per camera, AI can be switched off in the camera's options.

## 5. Monitoring

* Dashboard **Overview** + top-bar indicators for a quick look.
* `GET /api/v1/system/monitoring` (permission `devices:read`): backend, database, Redis, disk,
  every edge device (stale flag, CPU / RAM / disk / temperature), cameras with problems, AI problems,
  devices with an upload backlog. Use it for an external uptime checker.
* `GET /api/v1/health` — liveness (no auth); `GET /api/v1/system/status` — dependencies.
* Alerts (in-app and e-mail) for the conditions you configured.
* Logs: `docker compose logs -f api` (rotated: 5 × 20 MB per container); agent: `aci-agentctl logs`
  (Linux, journald) or Dashboard → Devices → This agent → System log.

## 6. Backups and restore

| What | How | Default |
|---|---|---|
| Central database | `scripts/server/backup.sh` (pg_dump, verified with pg_restore --list, sha256) | keep 14, run daily from cron |
| Restore | `scripts/server/restore.sh <file.dump>` (stops API, safety dump first, then restore) | — |
| Edge agent databases | automatic, daily, integrity-checked copies in `<data>/backups/`; *Backup now* in the dashboard | keep 7 |

Test a restore at least once on a spare machine — a backup you never restored is a hope, not a backup.

## 7. Updates and rollback

* **Server (Docker):** `scripts/server/update.sh v1.0.1` — backup → keep current images as
  `:rollback` → build and start the new version → health check for 2 min → automatic rollback of code
  and images if unhealthy (asks before restoring the database).
* **Edge agent (Linux):** `sudo aci-agentctl update <dir|archive>` keeps the previous version;
  `sudo aci-agentctl rollback` returns to it. **Windows:** update the folder, run
  `deploy\windows\install.ps1` again (data and configuration are kept).
* Migrations run automatically when the API starts.

## 8. Retention and deletion

Events and observations older than the configured retention are deleted hourly on the server and
during maintenance on agents. Agents also delete the oldest events when the disk is nearly full
(counted, reported). Deleted rows are overwritten on disk (SQLite `secure_delete`). A deleted user
loses access immediately; their audit-log entries are kept.

## 9. Audit log

Security-relevant actions (sign-ins, user / role changes, device enrollment / revocation,
configuration changes, alert rule changes, settings) are written to the audit log with user, time,
IP and details.
