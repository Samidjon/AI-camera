# API documentation (v1)

Interactive docs: `/docs` (Swagger) and `/redoc`. Machine-readable contract:
[`shared/contracts/openapi.json`](../shared/contracts/openapi.json) — regenerate with
`cd apps/api-server && uv run python -m scripts.export_openapi`.

Base path: `/api/v1`. All responses are JSON. Every response has an `X-Request-ID` header
(a client-supplied `X-Request-ID` is echoed back).

## Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | — | Liveness. `{"status":"ok","timestamp":...}`. Never checks dependencies. |
| GET | `/system/status` | — | Dependency health: `api`, `database`, `redis` with `state` (`ok` / `error` / `not_configured`), `latency_ms`, `detail` (database dialect when ok). Overall `status`: `ok`, `degraded` (Redis down), `error` (database down or not configured). |
| GET | `/system/version` | — | `name`, `version`, `api_version`, `python_version`, `environment` |
| POST | `/auth/login` | — | Body `{"email","password"}` → `{"access_token","token_type":"bearer","expires_in"}` |
| GET | `/auth/me` | Bearer | Current user with role and permissions |

Status endpoints are public in MVP 01 (no sensitive data; credentials are redacted from `detail`).
Restricting them is part of the security review in MVP 09.

## Authentication

```
Authorization: Bearer <access_token>
```

Tokens are HS256 JWTs signed with `ACI_JWT_SECRET`, valid for `ACI_ACCESS_TOKEN_EXPIRE_MINUTES`.
Claims: `sub` (user id), `role`, `perms` (role permissions — used by edge agents to authorize
locally), `type: "access"`, `iat`, `exp`.

The **edge agent** has its own API (`/api/v1` on port 8100, docs at `/docs`) — see
[edge/camera-agent/README.md](../edge/camera-agent/README.md) and
[CAMERA_INTEGRATION.md](CAMERA_INTEGRATION.md). Device runtime endpoints (`/api/v1/device*`) and the
**device ↔ central-server contract** (registration, heartbeat, `/events/batch`, configuration) that the
central server implements in MVP 06: [EDGE_AGENT.md](EDGE_AGENT.md).

### Edge devices, events, real time (MVP 06)

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/devices/register` | enrollment token | Device registration → device secret (once) |
| POST | `/devices/heartbeat` | device | Health report; reply: desired config version, rotation flag |
| POST | `/devices/credentials/rotate` | device | New device secret |
| POST / GET | `/devices/enrollment-tokens` | devices:manage | Create (token shown once) / list |
| DELETE | `/devices/enrollment-tokens/{id}` | devices:manage | Revoke token |
| GET | `/devices`, `/devices/{id}` | devices:read | List (filters `status`, `group_id`) / details incl. cameras |
| PATCH / DELETE | `/devices/{id}` | devices:manage | Rename, describe, group / revoke + delete |
| POST | `/devices/{id}/revoke`, `/devices/{id}/rotate-credentials` | devices:manage | Revoke / request rotation |
| GET | `/devices/{id}/heartbeats`, `/devices/{id}/sync-logs` | devices:read | History |
| GET | `/devices/{id}/configuration` | devices:read or the device | Current version (device: document + checksum) |
| PATCH | `/devices/{id}/configuration` | devices:manage | `{base_version, config, replace?, comment?}` → new version; 409 on conflict |
| POST | `/devices/{id}/configuration/rollback` | devices:manage | `{to_version, base_version}` |
| GET | `/devices/{id}/configuration/history` | devices:read | Versions with acknowledgement |
| POST | `/devices/{id}/configuration/ack` | device | `{version, status, applied, ignored, errors}` |
| GET / POST / PATCH / DELETE | `/device-groups[/{id}]` | read / manage | Device groups |
| POST | `/events/batch` | device | Idempotent upload → `{accepted, duplicate, rejected}` |
| GET | `/events` | events:read | Filters `device_id`, `camera_ref`, `event_type` (multi), `source`, `since`, `until`; cursor pagination |
| GET | `/events/{id}`, `/events/statistics`, `/events/statistics/summary` | events:read | Event / hourly aggregates / totals |
| WS | `/ws` | first message `{type:"auth", token}` | Live `device.status`, `camera.status`, `event.created`, `sync.status`, `system.notification` |

### Dashboard, users, settings (MVP 07)

| Method | Path | Permission | Description |
|---|---|---|---|
| GET | `/dashboard/overview?since=` | any (sections by permission) | Cameras, devices, AI, motion events today, people observations today, recent events |
| GET | `/cameras` | cameras:read | Camera registry across devices |
| GET | `/observations` | observations:read | Anonymous observations (one per track per camera); `since`, `until`, `device_id`, `camera_ref`, `limit`, `offset` |
| GET | `/events?category=&q=` | events:read | Categories `motion`, `person`, `zone`, `line`, `camera_error`, `ai_error`, `device_error`; search text |
| GET | `/events/export.csv` | events:export | Same filters, ≤100 000 rows |
| GET / POST | `/users` | users:manage | List / create |
| PATCH / DELETE | `/users/{id}` | users:manage | Role, active, name, password / delete (last administrator protected) |
| GET | `/roles`, `/roles/permissions` | users:manage | Roles with user counts / permission catalog |
| PUT | `/roles/{name}` | users:manage | Set permissions (not administrator) |
| GET / PATCH | `/system/settings` | any / settings:manage | Retention and privacy |
| GET | `/system/configuration` | any | Effective server configuration (no secrets) |

### Analytics, reports, alerts (MVP 08)

| Method | Path | Permission | Description |
|---|---|---|---|
| GET | `/analytics/summary?since=&until=&tz=&device_id=&camera_ref=` | events:read (people section: observations:read) | Standard (motion) and AI (people) analytics kept separate, health; hourly/daily series in the given IANA zone, weekday×hour heatmap |
| GET | `/analytics/ai-performance` | events:read | Inference FPS / latency per device from heartbeats |
| GET | `/analytics/reports/{daily\|weekly\|monthly\|camera\|device\|zone\|ai}?format=json\|csv\|pdf&day=` | events:read | Report for the period; `camera` needs `device_id`+`camera_ref`, `device` needs `device_id` |
| GET | `/alerts?status=open\|acknowledged\|resolved\|active&severity=` | events:read | Newest first |
| GET | `/alerts/counts` | events:read | Open / acknowledged / resolved |
| POST | `/alerts/{id}/acknowledge`, `/alerts/{id}/resolve`, `/alerts/acknowledge-all` | events:ack | |
| GET | `/alerts/{id}/deliveries` | events:read | In-app / e-mail delivery status, attempts, error |
| GET | `/alert-rules/catalog` | events:read | 14 alert kinds (group, label, default severity / threshold) + SMTP status |
| GET / POST | `/alert-rules` | events:read / settings:manage | List / create |
| PUT / DELETE | `/alert-rules/{id}` | settings:manage | Update / delete (raised alerts are kept) |
| POST | `/alert-rules/recommended` | settings:manage | Adds the 11 health rules (in-app only), idempotent |
| POST | `/alert-rules/test-email` | settings:manage | Sends a test e-mail via the configured SMTP |

WebSocket: `alert.created` (events:read) after the raising transaction commits.

Edge agent (MVP 07): `WS /api/v1/live` — multiplexed live video (first message `{type:"auth", token}`,
then `{type:"subscribe", cameras, layers, max_fps, max_width}`; binary frames). See [DASHBOARD.md](DASHBOARD.md).

Device authentication: `Authorization: Bearer <device secret>` + `X-ACI-Device-ID`. 429 responses carry
`Retry-After`. Details: [CENTRAL_SERVER.md](CENTRAL_SERVER.md).

Roles (seeded): `administrator` (`*`), `operator`, `viewer`. Endpoints added in later MVPs protect
themselves with `Depends(require_permission("cameras:control"))`.

## Errors

```json
{
  "error": {
    "code": "unauthorized",
    "message": "Invalid email or password",
    "details": null,
    "request_id": "7d0c5b2e..."
  }
}
```

| HTTP | `code` | When |
|---|---|---|
| 401 | `unauthorized` | Missing/invalid/expired token, wrong credentials |
| 403 | `forbidden` | Missing permission |
| 404 | `not_found` | Unknown route or resource |
| 409 | `conflict` | Duplicate resource |
| 422 | `validation_error` | Body/query validation; `details` = list of `{loc, msg, type}` |
| 503 | `database_not_configured` / `service_unavailable` | Dependency missing |
| 500 | `internal_error` | Unhandled error (details logged server-side only) |
