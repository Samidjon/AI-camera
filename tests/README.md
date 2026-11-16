# tests/

Cross-module tests live here (end-to-end, offline scenarios, performance). Each module keeps its own
unit/integration tests next to its code:

| Suite | Location | Run |
|---|---|---|
| Backend unit + API integration + migrations | `apps/api-server/tests/` | `uv run pytest` |
| PostgreSQL migration test (optional) | `apps/api-server/tests/migrations/` | set `TEST_DATABASE_URL` (disposable DB) |
| Dashboard build checks | `apps/dashboard` | `npm run lint && npm run typecheck && npm run build` |
| Everything | `scripts/check.ps1` / `scripts/check.sh` | |

Planned: Playwright E2E (MVP 07), offline / failure-recovery scenarios (MVP 05, MVP 09),
performance tests (MVP 09).
