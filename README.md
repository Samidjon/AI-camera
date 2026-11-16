# AI Camera Intelligence

Modular, offline-first video-surveillance platform: IP cameras → edge agents (local computer
vision, optional AI) → central server → web control center.

> **Status: MVP 02 — Camera Integration.** Central API server, database schema, auth, dashboard
> and the edge camera agent (RTSP / ONVIF / USB / file / FFmpeg sources, live preview, automatic
> reconnection, encrypted credentials) are implemented. Vision, AI, edge sync and analytics are
> **not implemented yet** (MVP 03–08). The dashboard labels every unimplemented module explicitly.

## Principles

- **Offline-first.** Edge agents (MVP 05) work without AI, internet, the central server, Redis or any
  cloud service. The central server and Supabase are optional sync targets for the edge.
- **AI is optional.** The standard (non-AI) vision engine is always available.
- **Privacy.** Temporary, camera-scoped track IDs only. No face recognition, no biometric matching.
- **No fake implementations.** Unbuilt features are marked "Not implemented · MVP NN", never mocked.

## Repository layout

```
apps/
  api-server/        Central API — Python 3.12, FastAPI, SQLAlchemy, Alembic
  dashboard/         Control center — Next.js 16, React 19, TypeScript, Tailwind 4, shadcn/ui
edge/camera-agent/   Edge camera agent — capture, camera manager, live preview (offline-first)
services/event-processor/  Event ingestion (placeholder until MVP 06)
shared/              Contracts: OpenAPI spec, JSON schemas, generated types
infrastructure/      Database (Supabase) and monitoring notes
docs/                Installation and API documentation
scripts/             check.ps1 / check.sh — run all checks
tests/               Cross-module tests (E2E, offline, performance — later MVPs)
```

## Quick start (Windows / Linux / macOS)

Requirements: **Python 3.12** with [uv](https://docs.astral.sh/uv/), **Node.js 20+**, a **Supabase**
project (or SQLite for local development). Docker is not required. Full guide:
[docs/INSTALLATION.md](docs/INSTALLATION.md).

```bash
# 1. API server
cd apps/api-server
cp .env.example .env          # set ACI_DATABASE_URL (Supabase) and ACI_JWT_SECRET
uv sync
uv run alembic upgrade head
uv run python -m app.cli create-user --email admin@example.com --role administrator
uv run uvicorn app.main:app --port 8000
```

```bash
# 2. Edge camera agent (second terminal)
cd edge/camera-agent
cp .env.example .env          # ACI_AGENT_JWT_SECRET = same value as the server's ACI_JWT_SECRET
uv sync
uv run python -m aci_agent    # http://localhost:8100
```

```bash
# 3. Dashboard (third terminal)
cd apps/dashboard
cp .env.example .env.local    # NEXT_PUBLIC_API_URL, NEXT_PUBLIC_AGENT_URL
npm install
npm run dev                   # http://localhost:3000
```

- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/api/v1/health · Status: `/api/v1/system/status` · Version: `/api/v1/system/version`

## Checks

```bash
powershell -ExecutionPolicy Bypass -File scripts/check.ps1   # Windows
./scripts/check.sh                                            # Linux / macOS
```

Runs backend and edge agent lint + tests, and dashboard lint + typecheck + production build.

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — modules, boundaries, decisions
- [CHANGELOG.md](CHANGELOG.md)
- [docs/INSTALLATION.md](docs/INSTALLATION.md) — setup with Supabase, troubleshooting
- [docs/API.md](docs/API.md) — endpoints and error format
- [docs/CAMERA_INTEGRATION.md](docs/CAMERA_INTEGRATION.md) — camera sources, statuses, preview, security, tests
- [infrastructure/database/README.md](infrastructure/database/README.md) — schema and Supabase security
