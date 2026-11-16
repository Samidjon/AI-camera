# Monitoring

MVP 01 provides the probes; dashboards/alerting are built in MVP 08–09.

| Probe | Meaning | Use for |
|---|---|---|
| `GET /api/v1/health` | Process is alive. Never touches dependencies. | Liveness (restart if failing) |
| `GET /api/v1/system/status` | Database / Redis checks with latency. `status`: `ok`, `degraded` (optional Redis down), `error` (database down or not configured). | Readiness, dashboards |
| `GET /api/v1/system/version` | Version / API version / runtime | Deploy verification |

Logs are JSON lines on stdout (`ACI_LOG_JSON=true`) with `request_id`; credentials are redacted.
Every response carries an `X-Request-ID` header for correlation.
