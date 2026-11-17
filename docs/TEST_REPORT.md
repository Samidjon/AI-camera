# Test report — release 1.0.0

Everything below was **measured**, on the hardware named, with the scripts in `tests/system/`
(raw results: `tests/system/results/*.json`). Nothing is extrapolated to other hardware.

## Test machine

| | |
|---|---|
| CPU | AMD Ryzen 7 7435HS (8 cores / 16 threads), laptop |
| RAM | 15.8 GB |
| GPU | NVIDIA GeForce RTX 3050 Laptop GPU (**not used by the AI engine** — see below) |
| OS | Windows 10 (19045), Python 3.12 |
| Video | `demo-parking.mp4`, 640 × 360 @ 15 fps, looped, as a file-source camera (one per simulated camera) |

## Offline scenarios

`tests/system/offline_scenarios.py` — real central server and edge agent processes, two cameras,
server killed / restarted, agent killed hard. Result 2026-11-16: **29 / 29 checks passed**.

| Scenario | Conditions | Verified | Measured |
|---|---|---|---|
| **A** | AI off, server off, no internet | cameras keep streaming; motion events written locally; events queued for upload; agent reports *offline* but keeps running; **no socket to any non-loopback address** (proof of no internet use); DB integrity ok | +10 local events in 30 s |
| **B** | AI on, server off | AI engine starts offline from the local model; inference keeps running; cameras keep streaming; standard engine keeps analysing next to AI; events still stored | 4.0 inferences/s, p95 279 ms (2 cameras) |
| **C** | AI switched off while running | AI → disabled; **cameras not restarted** (frame counters keep growing); standard engine keeps analysing and producing events | — |
| **D** | Agent process killed hard (crash / power loss), restarted on the same data | API back, both cameras online, same device identity and credentials, **no local event lost, upload queue preserved**, databases intact, AI setting persisted | API 5.9 s, cameras online 6.0 s after the kill |
| **E** | Server back after the outage (6.5 min) | queue drained; **server holds every local event, 0 duplicates, 0 dropped**; device online again | 48 queued events uploaded in 14.7 s |

Notes: "reboot" (D) is exercised as a hard kill of the whole process tree plus restart; the start at
boot itself is done by systemd / the Windows scheduled task. During the first runs two checks of B/C
failed because the *test* expected a new motion episode within 90 s — the sample video has continuous
traffic, so one episode legitimately lasts minutes. The check now counts every standard-engine event
(episodes and the per-minute activity summary) and also asserts that the analysed-frame counters grow.

## Performance

`tests/system/performance.py` — fresh stack per configuration, 20 s warm-up, 60 s measurement window,
sampling every 3 s. Run 2026-11-17 on an otherwise idle machine. CPU = the agent's process tree,
**100 % = one fully used logical core** (the machine has 16).

### Standard mode (no AI)

| Cameras | FPS per camera (mean / min) | Agent CPU (mean / p95) | % of machine | RAM | Events / min | Upload | Event DB growth | Event latency edge → server (median / p95) |
|---|---|---|---|---|---|---|---|---|
| 1 | 15.1 / 14.6 | 14 % / 25 % | 0.9 % | 154 MB | 4.8 | 6.0 kbit/s | ≈ 0 MB/h ¹ | 2.4 s / 3.0 s |
| 5 | 15.0 / 14.4 | 49 % / 66 % | 3.1 % | 298 MB | 34.9 | 10.5 kbit/s | 0.6 MB/h | 2.5 s / 4.0 s |
| 10 | 15.0 / 14.1 | 105 % / 132 % | 6.6 % | 474 MB | 67.7 | 13.9 kbit/s | 2.3 MB/h | 3.5 s / 4.8 s |
| 20 | 15.1 / 13.6 | 215 % / 248 % | 13.5 % | 819 MB | 132.2 | 23.4 kbit/s | 5.2 MB/h | 2.8 s / 4.8 s |

Every camera kept the full source frame rate up to 20 cameras. Cost grows linearly: ≈ 11 % of one core
and ≈ 35 MB RAM per 640×360 camera. The memory guard (default 1024 MB) never paused analysis.

### AI-enhanced mode (YOLOX-S person detection, configured 4 inferences/s per camera)

| Cameras | FPS per camera | Agent CPU (mean) | % of machine | RAM | AI inferences / s (all cameras) | ≈ per camera | Inference latency (avg / p95) | Timeouts | Event latency (median / p95) |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 15.0 | 513 % | 32 % | 241 MB | 3.9 | 3.9 /s | 113 / 139 ms | 0 | 2.4 / 2.7 s |
| 5 | 15.0 | 1200 % | 75 % | 391 MB | 7.8 | 1.6 /s | 128 / 164 ms | 0 | 2.8 / 4.5 s |
| 10 | 15.0 | 1166 % | 73 % | 563 MB | 8.4 | 0.8 /s | 119 / 143 ms | 0 | 2.8 / 4.8 s |
| 20 | 15.1 | 1065 % | 67 % | 912 MB | 6.2 | 0.3 /s | 160 / 206 ms | 0 | 2.6 / 5.3 s |

**Findings**

* **The AI engine runs on the CPU** (`backend: cpu`, OpenCV DNN). The RTX 3050 was not used; the GPU
  utilisation sampled by `nvidia-smi` (13–28 %) is system-wide (display, other programs) and did not
  change with AI load. A CUDA-enabled OpenCV build or a GPU provider is needed to use the GPU.
* On this machine total AI throughput saturates at **≈ 6–8 inferences/s**. The configured 4/s per
  camera is reached for **1 camera only**; with more cameras the engine shares that budget
  (≈ 1.6 /s per camera at 5, 0.8 at 10, 0.3 at 20). It degrades gracefully — no timeouts, cameras
  keep 15 fps and the standard engine keeps analysing every camera — but **people detection on 10–20
  cameras needs a GPU-backed machine** or a lower per-camera AI rate / fewer AI cameras.
* AI uses most of the CPU (65–75 % of 16 threads from 5 cameras on): don't run other heavy work on
  an AI edge machine of this class.

### Other measurements

* **Upload bandwidth** agent → server is small: 6–23 kbit/s for 1–20 cameras (events + heartbeats,
  measured with a counting proxy). **Camera ingest bandwidth was not measured** — file sources were
  used; for RTSP it equals the camera's stream bitrate (typically 0.5–4 Mbit/s per substream /
  main stream, set on the camera).
* **Storage:** the event database grows ≈ 5 MB/hour at 20 busy cameras (≈ 120 MB/day) with this
  sample's event rate; retention (default 30 days) and size limits (default 512 MB) bound it, the
  oldest events are pruned first.
* **Event processing latency** edge → stored on the server: median 2.4–3.5 s, p95 ≤ 5.3 s,
  dominated by the agent's upload interval (`ACI_AGENT_SERVER_SYNC_INTERVAL_S`, default 5 s).
* **AI in scenario B** (2 cameras, separate run): 4.0 inferences/s, p95 279 ms.

¹ With one camera, rows deleted by maintenance were reused; the file did not grow within 60 s.

## Functional test suites (release build)

| Suite | Result |
|---|---|
| Central server (pytest: unit, API integration, migrations on SQLite) | 105 passed, 1 skipped (PostgreSQL-only) |
| Edge agent (pytest) | 238 passed, 2 skipped |
| Browser end-to-end, Playwright (real server + agent + production build) | 15 / 15 |
| Offline scenarios A–E | 29 / 29 |

## Full chain, both modes

* **Standard mode:** camera (file source) → agent → standard engine → local SQLite → central server
  → dashboard — covered by Playwright and scenarios A, C, E.
* **AI-enhanced mode:** real USB webcam → agent → YOLOX + ByteTrack → local SQLite → central server
  → dashboard Observations / Analytics: verified with live data (147 person events, 75 detections /
  72 departures, delivered to the server with ≈ 3.8 s average delay).
* **Autonomous agent without server:** scenarios A, B, D.
* PostgreSQL: the production database; automated runs here used SQLite (same SQLAlchemy models and
  Alembic migrations; PostgreSQL migration test available with `TEST_DATABASE_URL`).

## How to reproduce

```bash
cd edge/camera-agent
uv run python ../../tests/system/offline_scenarios.py --outage-s 180
uv run python ../../tests/system/performance.py --cameras 1,5,10,20 --modes standard,ai
```

Run the performance script on the actual edge hardware (mini PC, Jetson) before deciding how many
cameras and AI cameras it can serve.
