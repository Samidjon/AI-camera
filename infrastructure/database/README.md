# Database (PostgreSQL on Supabase)

The central server uses PostgreSQL hosted on **Supabase**. Docker is not used.
Schema is owned by **Alembic** (`apps/api-server/alembic`) — do not create or change tables in the
Supabase Table Editor, or the migration history will drift.

## Set up

1. Create a project at supabase.com (choose the region closest to the server).
2. Project → **Connect** → copy a connection string:
   - **Session pooler** (port 5432, IPv4) — recommended for the API server and migrations;
   - Direct connection works if your network has IPv6;
   - Transaction pooler (port 6543) also works (prepared statements are disabled automatically).
3. Put it into `apps/api-server/.env` as `ACI_DATABASE_URL=...` (add `?sslmode=require`).
4. Run migrations and verify:

   ```bash
   cd apps/api-server
   uv run python -m app.cli check-db
   uv run alembic upgrade head
   ```

## Security notes

- Migration `0001` enables **Row Level Security** on every table. With no policies, Supabase's public
  REST/GraphQL API (anon / authenticated keys) cannot read or write these tables. The API server
  connects as the table owner (`postgres`) and is not affected.
- Never commit `.env`. Never put the `service_role` key or database password into the dashboard.
- Use a **separate** Supabase project (or branch) for `TEST_DATABASE_URL`: the PostgreSQL migration
  test drops all tables.

## Local development without Supabase

For offline development the API server can run on SQLite:
`ACI_DATABASE_URL=sqlite:///./dev.db`. Production must use PostgreSQL.

## Tables (migration 0002 — MVP 06)

`edge_devices`, `device_credentials` (SHA-256 hashes only), `device_enrollment_tokens` (hashes),
`device_groups`, `device_heartbeats`, `synchronization_logs`, `device_configurations` (immutable
versions, unique `(device_id, version)`), `event_statistics` (unique hourly bucket); `cameras` gains
`device_id` / `external_id` (unique pair), `camera_events` gains `device_id` / `camera_ref` /
`line_id` with index `(device_id, occurred_at)`. RLS enabled on all new tables. Details:
[docs/CENTRAL_SERVER.md](../../docs/CENTRAL_SERVER.md).

## Tables (migration 0001)

| Table | Purpose | Key constraints / indexes |
|---|---|---|
| `roles` | administrator / operator / viewer (seeded), JSONB permissions | unique `name` |
| `users` | dashboard accounts, bcrypt password hash | unique `email`, FK `role_id` RESTRICT |
| `camera_groups` | logical grouping | unique `name` |
| `cameras` | camera metadata (no secrets — MVP 02 adds an encrypted secret store) | CHECK source_type/status/fps/resolution, FK group SET NULL, idx status |
| `camera_events` | edge events, `id` = source event UUID | CHECK source, idx (camera_id, occurred_at), (event_type, occurred_at) |
| `system_logs` | server/component logs | CHECK level, idx level/component/created_at |
| `audit_logs` | append-only audit trail | FK user SET NULL, idx user/action/created_at |
