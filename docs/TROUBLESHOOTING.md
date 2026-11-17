# Troubleshooting

Start with **Dashboard → Overview** and `GET /api/v1/system/monitoring`; most problems are visible
there with a reason. Logs: server `docker compose logs --tail 200 api`, agent `aci-agentctl logs`
(Linux) or Dashboard → Devices → This agent → System log.

## Sign-in and connection

| Symptom | Cause / fix |
|---|---|
| Sign-in page asks for **Connection**, or "Central server is unreachable" | Enter the server address (https://…). From a phone use the public / tunnel address, not `localhost`. |
| "Mixed content" warning on the sign-in page | The dashboard is on https but the server address is http — browsers block that. Use an https address for the server (domain + Caddy, or a tunnel). |
| Sign-in works on the PC but not on the phone | `localhost` on the phone is the phone itself. Use the server's LAN IP / domain. |
| 401 after some time | Session expired (`ACI_ACCESS_TOKEN_EXPIRE_MINUTES`); sign in again. |
| CORS error in the browser console | The dashboard origin isn't allowed: add it to `ACI_CORS_ORIGINS` (or `ACI_CORS_ORIGIN_REGEX`) on the server and restart. |
| Cloudflare quick tunnel stopped working | Quick-tunnel addresses change on every restart. Use the new address, or a named tunnel / own domain. |

## Cameras

| Symptom | Cause / fix |
|---|---|
| Camera *offline*, error "unreachable" | Wrong IP / port, camera off, different network/VLAN. Test from the agent machine: `ffprobe rtsp://…` or Cameras → *Test*. |
| "auth_failed" | Wrong RTSP user / password; some cameras lock the account after several failures — wait, then retry. |
| "not_found" | Wrong RTSP path (stream URL differs per vendor) — use ONVIF discovery or the vendor's documentation. |
| Camera flips between *online* and *reconnecting* | Unstable Wi-Fi / overloaded camera. Lower the camera's bitrate / resolution; use a substream. |
| Scan finds nothing | Scanning covers private networks of the agent's own interfaces only; the camera must be in one of them and have RTSP or ONVIF enabled. |
| USB camera not found (Linux) | The service user needs the `video` group (the installer adds it); check `ls -l /dev/video*`. |

## AI engine

| Symptom | Cause / fix |
|---|---|
| AI *degraded*: model missing | Put the YOLOX ONNX file into `models/` (see [AI_ENGINE.md](AI_ENGINE.md)) and press *Enable*. |
| AI slow / timeouts, high latency | CPU-only inference: lower `process_fps`, use fewer AI cameras, or a machine with a GPU. Measured numbers: [TEST_REPORT.md](TEST_REPORT.md). Motion analysis is not affected. |
| No people analytics | AI is off, or it detected nobody in the period — that's an honest empty result. |

## Synchronisation (agent → server)

| Symptom | Cause / fix |
|---|---|
| Device *offline* on the server, cameras work locally | Network / server down, or wrong `ACI_AGENT_SERVER_URL`. The agent queues events and uploads them later (tested: scenario E). |
| Agent refuses `http://` server URL | Intentional. Use https, or for an isolated lab only `ACI_AGENT_SERVER_ALLOW_INSECURE_HTTP=true`. |
| Device *revoked* | An administrator revoked it — enroll again with a new token. |
| Queue `dropped` > 0 | The upload queue hit `ACI_AGENT_SERVER_SYNC_QUEUE_MAX` or the disk was nearly full during a long outage. Raise the limit / free disk. Events stay in the local database. |
| 429 Too Many Requests | Rate limit; the agent backs off automatically (Retry-After). |

## Server (Docker)

| Symptom | Cause / fix |
|---|---|
| `api` restarts: "ACI_JWT_SECRET must be set" | Production refuses default secrets — set it in `.env.production`. |
| `api` unhealthy after an update | `scripts/server/update.sh` rolls back automatically; check `docker compose logs api` for the migration error. |
| Caddy can't get a certificate | Ports 80 and 443 must be reachable from the internet and the DNS name must point to this host. |
| Disk filling up | `docker system df`; logs are rotated (5 × 20 MB per container); check backup retention in `/var/backups/aci`. |

## Edge agent

| Symptom | Cause / fix |
|---|---|
| Agent didn't start after reboot (Linux) | `systemctl status aci-agent`; `systemctl enable aci-agent`. |
| … (Windows) | Task Scheduler → *ACI Edge Agent* → History; re-run `deploy\windows\install.ps1` as administrator. |
| "storage paused" | Free disk below `ACI_AGENT_EVENTS_MIN_FREE_DISK_MB`: the agent stops writing events (cameras keep running) until space is available. |
| Database recovered / quarantined in the log | The agent found a corrupt database, moved it aside and started a clean one (or restored a backup). Keep the quarantined file for analysis. |
| Memory guard paused analysis | RSS above `ACI_AGENT_VISION_MAX_RSS_MB`: too many cameras for this machine. Fewer cameras / lower resolution, or raise the limit if RAM allows. |
