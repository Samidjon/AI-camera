# Web Dashboard (MVP 07)

`apps/dashboard` — Next.js 16, React 19, Tailwind 4, shadcn/ui. The control center for the whole
platform. **It contains no business logic of its own**: every number, status and list comes from the
central server API / WebSocket or from the edge agent API; the dashboard only displays and submits.

## Data sources

| What | From | Why |
|---|---|---|
| Fleet: devices, cameras registry, events, observations, users, settings, overview counters | Central server `/api/v1` + WebSocket `/api/v1/ws` | One view across all edge devices |
| Live video, camera management (add / edit / zones / lines), AI engine control, local events | The edge agent the dashboard is connected to (`NEXT_PUBLIC_AGENT_URL`) | Video and camera control stay on the local network, and keep working when the server is down |

When the central server is unreachable, fleet views show their last data as **stale** (banner on
every page, statuses shown as "Unknown — last known: …", never a false "Online"); local views (live
video, cameras, local events, this agent) keep working. When the server answers again, everything
refreshes automatically.

## Pages

| Page | Contents |
|---|---|
| **Overview** | Cameras total / online / offline, edge devices total / active, motion events today, people observations today (separate — never mixed), AI active devices, devices without AI, recent events, system health (server, DB, Redis, this agent). Updates live (debounced refresh on WebSocket messages). |
| **Live** | Monitoring wall: grid 1 / 4 / 9 / 16, camera selection, single camera view, fullscreen (wall or tile), overlays (motion regions, detection boxes + AI people, tracking IDs, zones & lines — drawn by the agent into the frames), displayed vs camera FPS, stream status per tile. |
| **Cameras** | List / grid, add / edit / delete, details, groups, location, connection status, test, network scan, vision configuration (zones, lines, detector). |
| **Devices** | Fleet (central): CPU, RAM, GPU, temperature, disk, network, cameras, AI status, configuration version, agent version, last heartbeat; device details with health, cameras, versioned configuration (history, rollback), sync log, credential rotation, revocation, deletion; add device (enrollment token). This agent: identity, hardware, server link, system log, backups. |
| **Events** | All devices (central): categories Motion, Person detection (AI), Zones, Line crossing, Camera errors, AI errors, Device errors; date range (presets or custom), camera filter, full-text search, pagination, CSV export (server-side, same filters, `events:export`), live "N new events" notice. This agent (local): works without the server. |
| **Observations** | Anonymous person observations: temporary track ID, camera, time, duration, direction, clothing attributes with confidence (if AI determined them and privacy settings allow), confidence, detection source. One card per track on one camera — never merged across cameras; no faces, no identification. |
| **AI Engine** | Enable / disable, model selection, version, status, inference FPS and latency, error log, recovery status, Standard vs AI-enhanced capabilities. |
| **Settings** | Account; Users (create, role, enable / disable, reset password, delete — last administrator protected); Roles & permissions matrix (operator / viewer editable, administrator fixed); Data & privacy (central event retention, observation retention, store / show clothing attributes); Cameras, devices, AI (fleet-wide configuration push: AI on devices / cameras, motion sensitivity, device event retention, heartbeat interval — each device gets a versioned, acknowledged configuration); System (effective server configuration, read-only, no secrets). |

## Roles

| Permission | Administrator | Operator | Viewer |
|---|:-:|:-:|:-:|
| cameras:read / events:read / devices:read | ✓ | ✓ | ✓ |
| cameras:control | ✓ | ✓ | |
| events:ack / events:export / observations:read | ✓ | ✓ | |
| cameras:manage / devices:manage / ai:manage / settings:manage | ✓ | | |
| users:manage | ✓ (only) | | |

Defaults; operator and viewer permissions can be changed in Settings → Roles & permissions. The API
enforces every permission (the UI only hides controls). Changes apply immediately on the server; edge
agents read permissions from the access token, so there they apply at the next sign-in (tokens live
60 minutes).

## Real-time

* **Central server** — one WebSocket per browser tab (`RealtimeProvider`), authenticated with the
  first message. Updates: device status (incl. health metrics, AI and camera summaries), camera
  status, new events, synchronization status, notifications, dashboard counters. Top bar shows
  *Live / Connecting / Reconnecting*; reconnection is automatic (exponential backoff 1 → 30 s,
  jitter). Agents report camera / AI status changes within ~5 s (early heartbeat), not only every 30 s.
* **Live video** — one WebSocket to the edge agent (`/api/v1/live`) carries **all** cameras of the
  wall: binary messages (`uint32` header length, JSON header, JPEG). The agent sends only the newest
  frame per camera at the requested rate and size (1 → 12 fps @ 1280 px … 16 → 6 fps @ 420 px);
  a slow browser gets fewer frames, never a backlog. Frames are decoded with `createImageBitmap` and
  painted on canvases. This replaces per-camera MJPEG connections (browsers allow ~6 per host), so
  16-camera walls work; MJPEG remains for single previews on the Cameras page.

## Design system

Dark by default (charcoal surfaces, one cool accent, status colours only for state), matching light
theme, theme switch in the top bar (dark / light / system, remembered per browser, applied before
first paint). Compact cards (`MetricTile`, `Panel`), consistent status indicator (dot + label, never
colour alone), responsive layout (sidebar collapses to a sheet below `lg`).

## Tests (Playwright)

`npm run test:e2e` starts the real stack on separate ports — central server (fresh SQLite, users
admin / operator / viewer), edge agent (fresh data, generated moving-object video → real motion
events), production build of the dashboard — and runs Microsoft Edge (`E2E_BROWSER_CHANNEL=chrome`
for Chrome; no browser download needed).

| Spec scenario | Spec |
|---|---|
| Login | `01-login-navigation` (wrong password, session required) |
| Navigation | `01-login-navigation` (every module, theme switch) |
| Camera management | `02-cameras` (add, edit, delete) |
| Device management | `03-devices` (enrollment token, agent registration, live fleet row, configuration v1 applied) |
| Live monitoring | `04-monitoring` (frames painted, one multiplexed socket, overlays, grid 1/4/9/16, single view) |
| AI settings | `05-ai` (enable / disable, honest status without a model) |
| Events + filters | `06-events` (categories, search, pagination, CSV export, local tab) |
| Permissions | `07-settings-permissions` (admin users / roles / retention; viewer read-only; operator) |
| WebSocket reconnection + offline behaviour | `08-realtime-offline` (server stopped → reconnecting, stale banner, "Unknown" instead of Online; restarted → live again, refreshed) |
