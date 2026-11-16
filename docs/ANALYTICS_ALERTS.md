# Analytics, reports and alerts (MVP 08)

Everything is computed on the **central server** from events the edge agents uploaded. Nothing is
estimated from video on the server and no number is invented: a period without data shows zeros for
motion and an explicit "no people analytics" notice for AI.

## Two kinds of analytics — never mixed

| | Standard analytics | AI analytics |
|---|---|---|
| Needs | nothing (motion detection runs on every agent) | AI engine enabled on the device and people detected |
| Source events | `motion_started/ended`, `object_entered/exited_zone`, `line_crossed` | `person_detected`, `person_left`, `person_crossed_line` |
| Counts | motion events, activity time, frequency, zone entries / exits, line crossings | people **observations** |
| Never | presented as a number of people | merged across cameras or over time |

* **Observation** = one temporary track on one camera (`track_id`); several events of the same track
  count once. The same person seen by two cameras, or leaving and coming back, is two observations —
  this is a privacy property, not a bug. It is not a unique-visitor count.
* **Inbound / outbound** = `person_crossed_line` forward / backward — the direction you drew the line.
* **Zone occupancy** = entries − exits in the period (never below 0): an *estimate*; objects that
  were already inside before the period start, or a missed exit, shift it.
* **Activity time** = sum of `duration_s` of `motion_ended`.
* Buckets (hour / day / weekday) use the IANA time zone of the request (the dashboard sends the
  browser zone). DST days have 23 / 25 hourly buckets. Range ≤ 400 days, ≤ 500 000 events per query.
* Device health / AI performance come from stored heartbeats (CPU, memory, temperature, disk,
  inference FPS, latency).

The `limitations` object in every response repeats these rules; the dashboard shows it under
"How these numbers are computed".

## Reports

`GET /api/v1/analytics/reports/{kind}?format=json|csv|pdf`

| kind | Period | Contents |
|---|---|---|
| daily / weekly / monthly | ends on `day` (default today, in `tz`) | standard + AI + health |
| zone | `since` / `until` | zones & lines |
| ai | `since` / `until` | AI performance per device + people analytics |
| device | `since` / `until`, `device_id` required | one device |
| camera | `since` / `until`, `device_id` + `camera_ref` required | one camera |

PDF uses a Unicode TTF (Arial on Windows, DejaVu on Linux; falls back to Latin-1 text). People
sections are omitted for roles without `observations:read`.

## Alerts

A **rule** = condition + scope (any / one device / one camera, optional zone or line) + severity +
cooldown + channels. Conditions:

| Group | Kind | Trigger |
|---|---|---|
| Camera | camera_offline / stream_error | `camera_error` (unreachable / auth / not found / invalid source → offline, other → stream error) |
| | camera_reconnected | `camera_recovered` — also resolves open offline / stream alerts of that camera |
| AI | ai_offline / model_error | `ai_error` (reason mentions the model → model error) |
| | ai_recovered | `ai_recovered` — resolves open AI-offline alerts |
| Device | device_offline | server stops receiving heartbeats |
| | high_cpu / high_memory / high_temperature / low_disk | heartbeat metric ≥ threshold (low disk: free GB ≤ threshold); defaults 90 %, 90 %, 85 °C, 5 GB |
| Motion | motion_detected / zone_activity / line_crossed | the corresponding event (line_crossed also for AI person crossings) |

* **Cooldown**: at most one alert per rule and scope (device + camera + zone + line) per cooldown.
* Alerts are evaluated inside the ingesting transaction and announced only **after commit**
  (WebSocket `alert.created`, e-mail); a rollback drops them. An error in alerting is logged and
  never rejects events or heartbeats.
* Status: open → acknowledged (`events:ack`) → resolved. Deleting a rule keeps its alerts.
* "Add recommended health rules" creates the 11 health rules with in-app notification only.

## E-mail (SMTP)

Set in the server `.env` and restart:

```
ACI_SMTP_HOST=smtp.gmail.com
ACI_SMTP_PORT=587
ACI_SMTP_USERNAME=you@gmail.com
ACI_SMTP_PASSWORD=<app password>
ACI_SMTP_FROM=you@gmail.com
ACI_SMTP_STARTTLS=true          # or ACI_SMTP_SSL=true with port 465
ACI_PUBLIC_DASHBOARD_URL=https://camera-agent-aci.vercel.app
```

A background worker sends each delivery with 3 attempts (5 s, 30 s apart) and records status,
attempts and the last error (`GET /alerts/{id}/deliveries`). Without SMTP, e-mail deliveries are
stored as `skipped` with the reason. Test from Alerts → Rules → "Send test e-mail".
