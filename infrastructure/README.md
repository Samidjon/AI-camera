# infrastructure/

- `database/` — Supabase/PostgreSQL setup and schema notes.
- `monitoring/` — health probes and logging conventions.

**Containers:** by decision of the project owner (2026-11-16) MVP 01 runs without Docker:
PostgreSQL is hosted on Supabase, the API server and dashboard run as native processes,
Redis is optional. `infrastructure/docker/` and `docker-compose.yml` from the original
MVP 01 spec are intentionally not created. Container/deployment packaging is revisited in MVP 09.
