# shared/

Language-neutral contracts shared by the API server, edge agent, event processor and dashboard.

| Folder | Content | Status |
|---|---|---|
| `contracts/` | `openapi.json` — exported from the API server (`scripts/export-openapi.ps1` / `.sh`). The source of truth for HTTP contracts. | MVP 01 |
| `schemas/` | JSON Schemas for payloads that cross process boundaries (edge → server). `camera-event.schema.json` mirrors the `camera_events` table. | MVP 01 |
| `types/` | Generated client types (TypeScript from OpenAPI). Generation is set up when the API surface grows in MVP 06/07; until then the dashboard keeps hand-written types in `apps/dashboard/src/lib/api/types.ts`. | planned |

Rules: contracts are versioned with the API (`/api/v1`); breaking changes require a new version.
