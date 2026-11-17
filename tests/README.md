# tests/

Cross-module tests live here (end-to-end, offline scenarios, performance). Each module keeps its own
unit/integration tests next to its code:

| Suite | Location | Run |
|---|---|---|
| Backend unit + API integration + migrations | `apps/api-server/tests/` | `uv run pytest` |
| PostgreSQL migration test (optional) | `apps/api-server/tests/migrations/` | set `TEST_DATABASE_URL` (disposable DB) |
| Dashboard build checks | `apps/dashboard` | `npm run lint && npm run typecheck && npm run build` |
| Everything | `scripts/check.ps1` / `scripts/check.sh` | |

| Browser end-to-end (real stack) | `apps/dashboard/e2e/` | `npm run test:e2e` |
| Offline scenarios A–E (real processes) | `tests/system/offline_scenarios.py` | `cd edge/camera-agent && uv run python ../../tests/system/offline_scenarios.py` |
| Performance 1/5/10/20 cameras | `tests/system/performance.py` | `cd edge/camera-agent && uv run python ../../tests/system/performance.py` |

System tests start their own server and agent processes on free ports with separate data
(`tests/system/.data/`, ignored); results are written to `tests/system/results/`. Report:
[docs/TEST_REPORT.md](../docs/TEST_REPORT.md).
