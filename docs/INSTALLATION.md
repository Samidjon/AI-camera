# Installation (development)

## Requirements

| Tool | Version | Notes |
|---|---|---|
| Python | 3.12 | managed by [uv](https://docs.astral.sh/uv/) (`uv python install 3.12`) |
| uv | 0.5+ | dependency/venv manager for the API server |
| Node.js | 20+ (tested 24) | for the dashboard |
| Supabase project | — | PostgreSQL for the central server. Optional for local dev (SQLite) |

Docker is **not** required.

> **Windows:** keep the project in a short path (e.g. `C:\Users\<you>\Desktop\ai-camera-intelligence`).
> Paths longer than 260 characters make Windows refuse to load native modules (psycopg, SQLAlchemy,
> Next.js SWC) with `DLL load failed` / error 206.

## 1. Supabase

1. Create a project at https://supabase.com.
2. **Connect** → copy the **Session pooler** URI (port 5432).
3. Details and security notes: [infrastructure/database/README.md](../infrastructure/database/README.md).

## 2. API server

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

## 3. Dashboard

```bash
cd apps/dashboard
cp .env.example .env.local     # NEXT_PUBLIC_API_URL=http://localhost:8000
npm install
npm run dev
```

Open http://localhost:3000 and sign in. If the dashboard runs on another origin, add it to
`ACI_CORS_ORIGINS` (JSON list) in the API server's `.env`.

## 4. Tests

```bash
cd apps/api-server && uv run pytest            # unit + integration + migrations (SQLite)
TEST_DATABASE_URL=<disposable postgres URL> uv run pytest -m postgres   # live PostgreSQL migrations
cd apps/dashboard && npm run lint && npm run typecheck && npm run build
```

Or everything at once: `scripts/check.ps1` (Windows) / `scripts/check.sh`.

> `TEST_DATABASE_URL` must point to a **separate** database — the test drops all tables.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `/system/status` → database `not_configured` | `ACI_DATABASE_URL` is empty in `apps/api-server/.env` |
| database `error` with timeout | Wrong host/port, or IPv6-only direct connection — use the Session pooler URI |
| `password authentication failed` | Reset the DB password in Supabase → Database settings |
| Dashboard: "Central server unreachable" | API not running, wrong `NEXT_PUBLIC_API_URL`, or origin missing from `ACI_CORS_ORIGINS` |
| `DLL load failed` (Windows) | Project path too long — move it to a short path |
| `Role 'administrator' not found` | Run `uv run alembic upgrade head` first |
