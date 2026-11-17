# User manual

For operators and viewers who use the web control center (browser or phone). Administrators:
see [ADMIN_MANUAL.md](ADMIN_MANUAL.md).

## Sign in

1. Open the address your administrator gave you (e.g. `https://cameras.example.com`).
2. If the sign-in page shows **Connection**, it needs the server address: enter the *Central server*
   URL (and, for live video on the local network, the *Edge agent* URL) → **Save**.
3. Enter e-mail and password → **Sign in**. Sessions expire after the configured time (default 60 min).

**On a phone:** open the same address in Chrome / Safari → menu → *Add to Home screen*. The app then
opens full-screen like a native app. It needs a connection to the server; it doesn't store video.

## What you see depends on your role

| Role | Can |
|---|---|
| Viewer | watch cameras, events, analytics; no people observations by default; can't change anything |
| Operator | everything a viewer can + control cameras, acknowledge alerts, export events, see observations |
| Administrator | everything, incl. users, roles, settings, alert rules |

Menu entries you are not allowed to use are hidden.

## Pages

**Overview** — cameras online / offline, devices, today's motion events and (separately) people
observations, recent events, system health. Updates live.

**Live** — video wall (1 / 4 / 9 / 16). Click a tile for a single camera, the expand icon for full
screen. Overlays: motion, detections, tracking IDs, zones / lines. A tile shows *Reconnecting* when
the camera stream drops; it recovers on its own.

**Cameras** — list and details; operators can connect / disconnect / test; administrators add, edit,
scan the network and draw zones and lines.

**Devices** — edge computers next to the cameras: status, CPU / RAM / disk / temperature, cameras,
AI state, last heartbeat. *Offline* means the server hasn't heard from the device — the device itself
keeps recording events and uploads them when the link returns.

**Events** — everything that happened: motion, zones, lines, people (AI), camera / AI / device
problems. Filter by category, date, camera, search text; **Export CSV** (operator+).

**Observations** — what the AI saw: one card per *temporary* track on one camera (time, duration,
direction, clothing colours with confidence). No faces, no identities; the same person on two cameras
is two separate observations.

**Analytics** — choose period and camera.
* *Standard analytics* (always available): motion events, activity time, motion per hour / day,
  weekday × hour heatmap, camera comparison, zones and lines. These count **movement**, not people.
* *AI analytics* (only if the AI engine detected people): observations, in / out through lines,
  time in view, busiest hours. Without AI you see "No people analytics" — never invented zeros.
* Every chart: hover for exact values, **Show table** for the numbers.
* **Reports**: daily / weekly / monthly / zones / AI / device / camera as CSV or PDF.

**Alerts** — the bell in the top bar shows open alerts. On the Alerts page: **Acknowledge** (you've
seen it), **Resolve** (it's handled), details show whether e-mails were sent.

## Status indicators

| Indicator | Meaning |
|---|---|
| **Live** (top bar) | real-time updates connected. *Reconnecting* = temporary; data may be a few seconds old |
| **Server online** | central server reachable |
| **AI on / off** | AI engine state of the edge agent |
| "Data may be stale" banner | the server is unreachable; you see the last known values |
| Unknown — last known | the device stopped reporting; the shown values are the last ones received |

## Privacy

The system does not recognise faces or identify people, does not estimate age, gender or ethnicity,
and does not store pictures of people. Track IDs are temporary and per camera. Retention of events
and observations is set by your administrator.
