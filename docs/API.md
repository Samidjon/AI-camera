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
[CAMERA_INTEGRATION.md](CAMERA_INTEGRATION.md).

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
