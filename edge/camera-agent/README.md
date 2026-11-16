# Edge Camera Agent — not implemented yet

Status: **placeholder (MVP 01)**. Implementation: camera capture in MVP 02, Standard Vision Engine in MVP 03,
optional AI in MVP 04, autonomous runtime in MVP 05.

## Non-negotiable constraints (from the engineering rules)

- Works **without** AI, internet, central server, Redis and any cloud service.
- Local storage is SQLite; events get a UUID at creation (idempotent sync).
- The central server (and Supabase) is an optional sync target, never a runtime dependency.
- Must not import anything from `apps/api-server`. Shared payloads go through `shared/schemas/`.
