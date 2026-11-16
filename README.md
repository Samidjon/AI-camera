# AI Camera Intelligence

Modular, offline-first video-surveillance platform: IP cameras → edge agents (local computer
vision, optional AI) → central server → web control center.

> **Status: MVP 04 — AI Engine.** Everything below plus an optional, fail-safe AI layer (local YOLOX
> person detection, ByteTrack, clothing attributes, optional Claude provider).
>
> **MVP 03 — Standard Vision Engine.** Central API server, auth, dashboard, the edge camera
> agent (RTSP / ONVIF / USB / file / FFmpeg sources, live preview, reconnection, encrypted
> credentials) and AI-free motion analysis (motion objects, tracking, line crossing, zones, local
> event storage) are implemented and work without internet or server. Optional AI, edge sync and
> analytics are **not implemented yet** (MVP 04–08).

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
edge/camera-agent/   Edge agent — capture, vision, AI, local storage, sync, Linux/Jetson installer (offline-first)
services/event-processor/  Event Processor notes (module lives in apps/api-server/app/processing)
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
- [docs/VISION_ENGINE.md](docs/VISION_ENGINE.md) — motion detection, tracking, lines, zones, events, storage
- [docs/AI_ENGINE.md](docs/AI_ENGINE.md) — optional AI: model install, modes, failure protection, privacy
- [docs/CENTRAL_SERVER.md](docs/CENTRAL_SERVER.md) — devices, event processor, configuration, WebSocket, security
- [docs/EDGE_AGENT.md](docs/EDGE_AGENT.md) — edge runtime: identity, offline sync, server contract, backups, security
- [docs/JETSON.md](docs/JETSON.md) — install on Linux / NVIDIA Jetson as a systemd service, update, uninstall
- [infrastructure/database/README.md](infrastructure/database/README.md) — schema and Supabase security
