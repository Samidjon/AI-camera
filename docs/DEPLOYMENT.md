# Deployment

Three parts, three places — by design (offline-first):

| Part | Where | Why |
|---|---|---|
| **Dashboard** (`apps/dashboard`, Next.js) | **Vercel** (or any Node host) | Static + client app; installable on phones (PWA) |
| **Central server** (`apps/api-server`, FastAPI) | A machine that keeps a process running: your PC / a server / Render / Railway — reachable over **HTTPS** | Needs WebSockets, a background device monitor and a database (Supabase PostgreSQL) |
| **Edge agent** (`edge/camera-agent`) | The computer **next to the cameras** (PC, mini PC, NVIDIA Jetson) | Talks to cameras over USB / RTSP on the local network. It can't run on Vercel or any cloud function |

## Dashboard on Vercel

Project settings → Build and Deployment:

* **Root Directory:** `apps/dashboard`
* **Framework Preset:** Next.js (build / install / output: defaults)
* Environment variables (optional defaults, users can change them on the sign-in page):
  `NEXT_PUBLIC_API_URL=https://<your server>`, `NEXT_PUBLIC_AGENT_URL=https://<your agent>`

The sign-in page has a **Connection** section: the central server address (and the edge agent
address) are stored in the browser, so one deployment works with any installation and with tunnel
addresses that change.

## Central server reachable over HTTPS

Quickest (no account): a Cloudflare quick tunnel on the machine that runs the server.

```bash
cloudflared tunnel --url http://127.0.0.1:8000     # prints https://<random>.trycloudflare.com
```

The address changes on every start — enter the new one under *Connection* on the sign-in page.
For a fixed address use a named Cloudflare tunnel with your own domain, or a host like Render.

Server `.env` (never commit it):

```bash
ACI_DATABASE_URL=postgresql://…               # Supabase
ACI_JWT_SECRET=<random, 32+ chars>             # same value as the agent's ACI_AGENT_JWT_SECRET
ACI_CORS_ORIGINS=["http://localhost:3000"]
ACI_CORS_ORIGIN_REGEX=https://camera-agent(-[a-z0-9-]+)?\.vercel\.app   # every Vercel deployment of the project
```

## Edge agent and remote viewing

The agent's API (live video, camera management) is meant for the local network. A dashboard opened
**on the same PC** reaches it at `http://localhost:8100` even from the Vercel site. To reach live
video from a phone outside the LAN, the agent needs its own HTTPS address (a second tunnel or a
reverse proxy with TLS) — a deliberate decision, because it exposes camera control to the internet
(protected by sign-in and permissions). Add the dashboard origin to `ACI_AGENT_CORS_ORIGINS` /
`ACI_AGENT_CORS_ORIGIN_REGEX`.

## Phone

Open the dashboard URL on the phone → browser menu → **Add to Home screen** (Android Chrome:
"Install app"; iPhone Safari: Share → "Add to Home Screen"). It opens full-screen with its own icon;
the app shell also opens offline and then shows the "server unreachable" state instead of a browser
error. Data is always live from the server — nothing is cached.

## Security checklist

* Never commit `.env`, `.dev-admin.env`, `*.db`, `data/`, `secret.key` (all in `.gitignore`).
* If a secret was ever pushed: rotate it (database password, `ACI_JWT_SECRET` on server + agent,
  user passwords) — removing the file from history does not un-publish it.
* Use HTTPS for every address the dashboard and the agents use.
