# Changelog

All notable changes are documented here. Format: [Keep a Changelog](https://keepachangelog.com/),
versioning: [SemVer](https://semver.org/).

## [0.1.1] — MVP 01 fixes found on Supabase — 2026-11-16

### Fixed
- Migration `0001` failed on PostgreSQL: the role seed bound `permissions` as VARCHAR into a JSONB
  column (SQLite accepted it). Now bound as JSON online and as a string literal in `--sql` mode.
  Verified upgrade → downgrade → upgrade on Supabase (PostgreSQL 17).
- Database URLs from Supabase with `?pgbouncer=true` (Prisma/ORM tab) were rejected by psycopg;
  ORM-only query parameters are now stripped.
- Raw passwords containing `/ ? # @ [ ]` broke URL parsing; the password part is now
  percent-encoded once, automatically.

## [0.1.0] — MVP 01 Core Foundation — 2026-11-16

### Added
- Monorepo structure: `apps/`, `edge/`, `services/`, `shared/`, `infrastructure/`, `docs/`, `scripts/`, `tests/`.
- **API server** (FastAPI, Python 3.12): API versioning (`/api/v1`), environment configuration,
  structured JSON logging with request IDs and credential redaction, centralized error envelope,
  request validation, OpenAPI docs (`/docs`, `/redoc`).
- Endpoints: `GET /api/v1/health`, `GET /api/v1/system/status`, `GET /api/v1/system/version`,
  `POST /api/v1/auth/login`, `GET /api/v1/auth/me`.
- Auth foundation: bcrypt password hashing, JWT access tokens, roles with permissions
  (administrator / operator / viewer), `require_permission` guard, login audit trail.
- **Database**: Alembic migration `0001` with `users`, `roles`, `cameras`, `camera_groups`,
  `camera_events`, `system_logs`, `audit_logs` — indexes, FKs, CHECK constraints, seeded roles,
  Row Level Security for Supabase.
- Admin CLI: `create-user`, `check-db`.
- **Dashboard** (Next.js 16, Tailwind 4, shadcn/ui, Lucide): dark surveillance control-center
  design system, sidebar + top bar shell, login, Overview (live server health/version/uptime),
  Cameras / Devices / Events / Analytics (explicitly marked as not implemented), Settings
  (account, connection, permissions). Stale-data and server-unreachable states.
- Shared contracts: `shared/contracts/openapi.json`, `shared/schemas/camera-event.schema.json`.
- Tests: backend unit, API integration, permission guard, migration tests (SQLite cycle, model/migration
  drift, PostgreSQL DDL generation, optional live PostgreSQL); dashboard lint, typecheck, build.
- `scripts/check.ps1` / `scripts/check.sh`.

### Changed (vs. original MVP 01 spec)
- No Docker / `docker-compose.yml`: PostgreSQL is hosted on Supabase; Redis is optional.
