# Security and privacy

Result of the MVP 09 review. Each item states what is implemented and how it is verified.
Report vulnerabilities privately to the maintainer (not in public issues).

## Authentication

| | Implementation | Verified by |
|---|---|---|
| Users | bcrypt password hashes (cost 12), passwords > 72 bytes rejected (no silent truncation), min. length 10 for new passwords | `tests/unit/test_security.py` |
| Sessions | HS256 JWT, short-lived (default 60 min), secret ≥ 32 chars **required in production** (server refuses to start otherwise) | `test_config.py`, `test_production.py::test_auth_edge_cases` (tampered signature, truncated token, `alg=none` forgery → 401) |
| Login throttling | token buckets per client IP (10/min) and per account (30/h), before the password check; 429 + `Retry-After` | `test_production.py::test_login_*` |
| Edge devices | one-time enrollment token (24 h) → device-specific random secret (only its SHA-256 is stored on the server); rotation and revocation | `test_devices.py` |
| Agent API | accepts dashboard JWTs signed with the shared secret, permission-checked per route; binds to 127.0.0.1 by default | agent tests |

## Authorization

Role-based permissions (`cameras:read/control/manage`, `devices:read/manage`, `events:read/ack/export`,
`observations:read`, `users:manage`, `settings:manage`, …) enforced on **every** server and agent
route; the dashboard only hides what the API would refuse anyway. The last administrator cannot be
demoted or deleted. People data (`observations:read`) is not granted to viewers by default.
Verified: `test_permissions.py`, `test_dashboard_api.py`, Playwright `07-settings-permissions`.

## API security

* **Input validation**: Pydantic models with length / range limits on every input; unknown fields
  rejected where it matters; invalid UUIDs / time zones / ranges → 4xx, never 500
  (`test_production.py::test_injection_and_validation`).
* **SQL injection**: SQLAlchemy parameters on the server; `?` placeholders on the agent. The few
  dynamic SQL fragments interpolate only constants / whitelisted column names (reviewed). Search
  strings with SQL metacharacters are tested to be treated as data.
* **XSS**: the dashboard renders all text through React (escaped); no user content in
  `dangerouslySetInnerHTML` (the only use is a static theme script). CSP limits scripts to the
  dashboard origin; API responses are JSON with `Content-Security-Policy: default-src 'none'`.
* **CSRF**: not applicable by design — authentication uses an `Authorization: Bearer` header, never
  cookies; CORS never allows credentials.
* **Rate limiting**: login, device registration, heartbeats, event uploads (configurable).
* **Security headers** (server, agent, dashboard): `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, `Cache-Control:
  no-store` on API responses, HSTS when served over HTTPS.
* **CORS**: explicit origin list (+ optional regex for preview deployments).

## TLS

* Docker Compose: Caddy terminates TLS with automatic Let's Encrypt certificates for the configured
  domain; the database and Redis are on an internal network without published ports.
* Edge agent → server: HTTPS required; plain HTTP only with an explicit lab flag; custom CA bundle
  supported (`ACI_AGENT_SERVER_CA_FILE`).
* Supabase connections use `sslmode=require`.

## Secrets management

* Secrets only in environment files that git ignores (`.env`, `.env.production`, `.dev-admin.env`),
  mode 600 / restricted ACL (Linux installer: root-owned `/etc/aci-agent/agent.env`; Windows
  installer: `C:\ProgramData\aci-agent` readable by SYSTEM and Administrators only).
* Camera credentials: encrypted at rest on the agent (Fernet, key file mode 600), never sent to the
  server, never returned by the API.
* Device secrets: stored encrypted on the agent, hashed on the server.
* Containers run as non-root with `no-new-privileges`, dropped capabilities, read-only root (API).
* **Incident note:** the database password of the development Supabase project was committed once in
  an early history of the public repository. History was rewritten, the JWT secret and admin password
  were rotated; **the Supabase password must be rotated by the project owner**. GitHub may still serve
  the old commit by its hash until it is garbage-collected — treat that password as public.

## Audit logs

Sign-ins (success / failure), user and role changes, device enrollment / rotation / revocation /
deletion, configuration changes and rollbacks, settings and alert-rule changes are written to
`audit_logs` with user, IP, time and details. Agents keep a local system log.

## Privacy

| Requirement | Implementation |
|---|---|
| Anonymous temporary track IDs | ByteTrack IDs are per camera and per session; never linked across cameras or over time |
| No hidden face recognition | No face detector / recogniser exists in the code base; person detection is a generic object detector (YOLOX "person" class) |
| No biometric identity matching | No embeddings / re-identification features are computed or stored |
| No sensitive attribute inference | Attribute schema has only clothing colour / type, bag, backpack, headwear; the optional Claude provider is constrained by a JSON schema without identity, age, gender, ethnicity, emotion fields and instructed to refuse them |
| No stored images of people / no continuous video | Frames are processed in memory; the agent writes no images or video to disk. Live view is streamed on demand only |
| Configurable retention | Settings → Data & privacy (server); agent retention days + size limits |
| Secure deletion | Retention jobs delete rows; SQLite `secure_delete` overwrites deleted content in the agent databases (`test_storage_security.py`); PostgreSQL: deleted tuples are reclaimed by autovacuum |
| Access control | `observations:read` required for people data; attributes can be hidden or not stored at all |
| Audit trail | see above |

## Operator checklist

- [ ] Strong, unique `ACI_JWT_SECRET` and `POSTGRES_PASSWORD`; `.env.production` mode 600
- [ ] HTTPS domain configured (no plain HTTP outside a closed LAN)
- [ ] Default / shared accounts removed; one account per person; least-privilege roles
- [ ] Recommended health alert rules enabled; e-mail tested
- [ ] Daily backup in cron; one restore tested
- [ ] Cameras on a separate VLAN, camera default passwords changed, camera cloud features off
- [ ] Agent API not exposed to the internet (it binds to 127.0.0.1 unless you change it)
