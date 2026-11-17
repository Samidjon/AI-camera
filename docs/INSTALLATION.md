# Installation

| Goal | Section |
|---|---|
| Production server (any Linux host with Docker) | [A. Central server with Docker Compose](#a-central-server-with-docker-compose) |
| Edge agent on Linux mini PC / NVIDIA Jetson | [B. Edge agent on Linux](#b-edge-agent-on-linux-mini-pc-jetson) |
| Edge agent on a Windows mini PC | [C. Edge agent on Windows](#c-edge-agent-on-windows) |
| Dashboard on Vercel / phone | [DEPLOYMENT.md](DEPLOYMENT.md) |
| Development on your computer | [Development](#development) |

## A. Central server with Docker Compose

Requirements: a Linux host with Docker Engine 24+ and the compose plugin, a DNS name pointing to the
host and ports 80/443 reachable (for automatic HTTPS). Closed LAN without a domain: see the comment in
the env template. Size the host by your event volume; the server itself is light (see
[TEST_REPORT.md](TEST_REPORT.md) for measured event latencies).

```bash
git clone https://github.com/<you>/AI-camera.git /opt/aci && cd /opt/aci
git checkout v1.0.0
cp .env.production.example .env.production && chmod 600 .env.production
# fill in ACI_SITE_ADDRESS, ACI_PUBLIC_URL, POSTGRES_PASSWORD, ACI_JWT_SECRET (and SMTP if wanted)
docker compose --env-file .env.production up -d --build
docker compose --env-file .env.production exec api python -m app.cli create-user \
    --email admin@example.com --name "Admin" --role administrator        # asks for a password
```

Open `https://<your domain>` and sign in. Then:

* daily backup: `sudo crontab -e` → `30 2 * * * /opt/aci/scripts/server/backup.sh >> /var/log/aci-backup.log 2>&1`
* updates: `scripts/server/update.sh v1.0.1` (backup + automatic rollback), see [ADMIN_MANUAL.md](ADMIN_MANUAL.md)
* uninstall: `scripts/server/uninstall.sh` (keeps data) / `--purge`

PostgreSQL 16 and Redis are not published to the network; only Caddy listens on 80/443. Container
logs are rotated (5 × 20 MB). To use Supabase instead of the bundled PostgreSQL, set
`ACI_DATABASE_URL` for the `api` service and remove the `postgres` service.

## B. Edge agent on Linux (mini PC, Jetson)

On the computer next to the cameras (x86_64 or aarch64, systemd):

```bash
sudo edge/camera-agent/deploy/linux/install.sh [--with-claude]
sudo aci-agentctl configure        # server URL, enrollment token (Dashboard → Devices → Add device)
aci-agentctl status
```

Starts at boot, restarts after a crash (`Restart=always`), runs as an unprivileged user with systemd
sandboxing. Updates with checksum and rollback, uninstall, Jetson specifics: [JETSON.md](JETSON.md).
Mini PCs (Intel N100 / Core / Ryzen class) use the same installer. Measured capacity:
[TEST_REPORT.md](TEST_REPORT.md) — measure on your own hardware before sizing.

## C. Edge agent on Windows

Elevated PowerShell in the project folder (requires [uv](https://docs.astral.sh/uv/)):

```powershell
.\edge\camera-agent\deploy\windows\install.ps1 -ServerUrl https://cameras.example.com -EnrollmentToken <token>
```

Registers the scheduled task *ACI Edge Agent* (runs as SYSTEM at start-up, before any login;
restarts every minute after a failure). Data and configuration: `C:\ProgramData\aci-agent`
(SYSTEM + Administrators only). Set `ACI_AGENT_JWT_SECRET` in `C:\ProgramData\aci-agent\.env` to the
server's `ACI_JWT_SECRET` so dashboard sign-ins are accepted by the agent. Remove:
`deploy\windows\uninstall.ps1` (data kept) / `-Purge`.

## Development

### Requirements

| Tool | Version | Notes |
|---|---|---|
| Python | 3.12 | managed by [uv](https://docs.astral.sh/uv/) (`uv python install 3.12`) |
| uv | 0.5+ | dependency/venv manager for the API server |
| Node.js | 20+ (tested 24) | for the dashboard |
| Supabase project | — | PostgreSQL for the central server. Optional for local dev (SQLite) |

Docker is not required for development.

> **Windows:** keep the project in a short path (e.g. `C:\Users\<you>\Desktop\ai-camera-intelligence`).
> Paths longer than 260 characters make Windows refuse to load native modules (psycopg, SQLAlchemy,
> Next.js SWC) with `DLL load failed` / error 206.

### 1. Supabase

1. Create a project at https://supabase.com.
2. **Connect** → copy the **Session pooler** URI (port 5432).
3. Details and security notes: [infrastructure/database/README.md](../infrastructure/database/README.md).

### 2. API server

```bash
cd apps/api-server
cp .env.example .env
```

Edit `.env`:

```ini
ACI_DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
ACI_JWT_SECRET=<python -c "import secrets; print(secrets.token_urlsafe(48))">
ACI_REDIS_URL=            # optional
```

```bash
uv sync                                     # creates .venv with Python 3.12
uv run python -m app.cli check-db           # → Database connection OK
uv run alembic upgrade head                 # creates tables + roles
uv run python -m app.cli create-user --email admin@example.com --name "Admin" --role administrator
uv run uvicorn app.main:app --port 8000 --reload
```

Offline development without Supabase: `ACI_DATABASE_URL=sqlite:///./dev.db`.

### 3. Dashboard

```bash
cd apps/dashboard
cp .env.example .env.local     # NEXT_PUBLIC_API_URL=http://localhost:8000
npm install
npm run dev
```

Open http://localhost:3000 and sign in. If the dashboard runs on another origin, add it to
`ACI_CORS_ORIGINS` (JSON list) in the API server's `.env`.

### 4. Tests

```bash
cd apps/api-server && uv run pytest            # unit + integration + migrations (SQLite)
TEST_DATABASE_URL=<disposable postgres URL> uv run pytest -m postgres   # live PostgreSQL migrations
cd apps/dashboard && npm run lint && npm run typecheck && npm run build
```

Or everything at once: `scripts/check.ps1` (Windows) / `scripts/check.sh`.

> `TEST_DATABASE_URL` must point to a **separate** database — the test drops all tables.

### 4b. Browser end-to-end tests (Playwright)

```bash
cd apps/dashboard
npm run test:e2e                      # Microsoft Edge (installed on Windows)
E2E_BROWSER_CHANNEL=chrome npm run test:e2e
```

Starts its own central server (:18000, fresh SQLite), edge agent (:18100, generated test video) and a
production build of the dashboard (:13000); nothing of your running instances is touched. Needs `uv`
and the Python dependencies of both services. `ACI_E2E=1 scripts/check.ps1` includes it in the checks.

### 4c. System tests (offline scenarios, performance)

```bash
cd edge/camera-agent
uv run python ../../tests/system/offline_scenarios.py     # scenarios A–E, ~8 min
uv run python ../../tests/system/performance.py           # 1/5/10/20 cameras, standard + AI
```

Real server and agent processes on free ports with their own data; results in `tests/system/results/`.

## Troubleshooting (development)

Production problems: [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

| Symptom | Cause / fix |
|---|---|
| `/system/status` → database `not_configured` | `ACI_DATABASE_URL` is empty in `apps/api-server/.env` |
| database `error` with timeout | Wrong host/port, or IPv6-only direct connection — use the Session pooler URI |
| `password authentication failed` | Reset the DB password in Supabase → Database settings |
| Dashboard: "Central server unreachable" | API not running, wrong `NEXT_PUBLIC_API_URL`, or origin missing from `ACI_CORS_ORIGINS` |
| `DLL load failed` (Windows) | Project path too long — move it to a short path |
| `No Python at '...\AppData\Roaming\uv\python\...'` | The venv was created by a sandboxed/packaged app whose AppData is virtualized. In your own terminal: `uv python install 3.12 --reinstall`, delete `.venv`, `uv sync` |
| `password authentication failed for user "postgres"` | Wrong DB password — **remove the square brackets** of the `[YOUR-PASSWORD]` template, or reset the password in Supabase → Database settings |
| `invalid connection option "pgbouncer"` | Fixed automatically since 0.1.1 (ORM-only params such as `pgbouncer=true` are stripped). Passwords with `/ ? # @ [ ]` are percent-encoded automatically |
| `Role 'administrator' not found` | Run `uv run alembic upgrade head` first |
