# Standard Vision Engine (MVP 03)

AI-free motion analysis inside the Edge Camera Agent. Runs entirely on the edge device: **no neural
networks, no cloud AI, no external APIs, no central server, no internet.** It is a complete working
system on its own; the optional AI engine (MVP 04) will add to it, never replace it.

> Without AI the engine cannot know *what* moved. Everything it detects is a **motion object** —
> never a "person". Counts are motion counts and occupancy is an *estimate*.

## Pipeline

```
capture worker ─► FrameQueue(2, drop-oldest) ─► VisionWorker (one thread per camera)
    downscale → gray → Gaussian blur → foreground mask → threshold → zone mask
    → morphology (open / close / dilate) → contours → min-area filter → merge fragments
    → MotionTracker → ZoneMonitor + LineCrossingCounter → events → EventStore (async writer)
                                              └─► VisionState → overlays + API
```

## Motion detection (OpenCV)

| Algorithm | `algorithm` | When to use |
|---|---|---|
| Frame differencing | `frame_diff` | Fastest; reacts only to change between consecutive frames |
| Background subtraction (running average) | `background` | Cheapest model; adapts slowly |
| MOG2 | `mog2` (default) | Robust general choice; handles gradual light changes; shadows ignored |
| KNN | `knn` | Dynamic backgrounds (foliage, water) |

Settings (per camera, `PUT /cameras/{id}/vision/config`): `sensitivity` (1–100 → pixel threshold
60…8), `min_object_area` (fraction of frame), `merge_distance`, `blur_kernel` and
`morphology_iterations` (noise reduction), `process_width` (analysis resolution), `process_fps`
(frame skipping), `restrict_to_zones`, `motion_end_delay_s`, `lighting_change_ratio`, tracker
gates, `activity_interval_s`.

- **Lighting change** (lights on/off, IR switch, auto exposure): when a large part of the frame changes
  at once, the background model is reset and no motion is reported for that frame.
- **Empty / corrupt frame**: ignored, state untouched.
- **Detection zones**: `exclude` zones are masked out (trees, screens, clocks); with
  `restrict_to_zones` only `monitor` zones are analysed.

## Motion objects and tracking

Each region reports appearance/disappearance (via the tracker), size, direction (compass heading of
its recent path) and duration. The tracker associates regions frame to frame by centroid distance +
bounding-box overlap (greedy, distance-gated); a track must be matched twice before it is
confirmed (filters flicker) and survives `track_max_missed` analysed frames without a match.

**Motion Track IDs** (`m-<camera>-<n>`) are temporary: scoped to one camera and one agent session, reset
on restart, never reused, and never a person identifier.

## Lines and zones

Drawn in the dashboard (Cameras → camera → *Detection & zones*), stored in normalized 0..1
coordinates. The reference point of a motion object is the bottom-centre of its box.

- **Crossing line** A→B: a crossing is counted when the point changes side *and* the movement passes
  through the segment; positions exactly on the line are ignored; a per-track cooldown stops jitter
  from double-counting. Direction `forward` = along the normal (dy, −dx) of A→B (the dashed arrow in
  the editor), `backward` = opposite. Counts: per session (live) and per day (from stored events).
- **Zone** (`monitor`): `object_entered_zone` / `object_exited_zone` (with dwell time; an object that
  stops moving inside the zone exits with `track_lost: true`), zone activity and occupancy estimate
  (motion objects currently inside).

## Events

| Type | When | Key metadata |
|---|---|---|
| `motion_started` | first confirmed motion object after a quiet period | objects, motion_ratio, boxes |
| `motion_ended` | `motion_end_delay_s` without motion, stream lost, or analysis stopped | duration_s, max_objects, reason |
| `object_entered_zone` / `object_exited_zone` | track anchor enters / leaves a monitor zone | zone_name, dwell_s, heading |
| `line_crossed` | track crosses a line | line_name, direction, heading |
| `camera_motion_activity` | every `activity_interval_s` with any motion | active_ratio, active_s, max_objects |

Each event has a UUID generated on the device (`id`), `camera_id`, `event_type`, `occurred_at`,
`zone_id`, `line_id`, `track_id`, `metadata` and `source = "standard"`. API: `GET /events`
(filters: camera, type, since/until; keyset pagination), `GET /events/summary`.

## Local storage

Events live in `<data_dir>/events.db` (SQLite, WAL), separate from configuration (`agent.db`).

- **Non-blocking writer**: events go through a bounded queue (default 10 000) to one writer thread that
  inserts in batches. A full queue drops events (counted) instead of blocking analysis.
- **Retention**: events older than `ACI_AGENT_EVENTS_RETENTION_DAYS` (30) are deleted; above
  `ACI_AGENT_EVENTS_MAX_DB_MB` (512, live pages) the oldest 10 % are pruned.
- **Disk monitoring**: below `ACI_AGENT_EVENTS_MIN_FREE_DISK_MB` (500) the oldest 20 % are pruned; below
  half of it writing pauses until space returns (capture and analysis continue).
- **Integrity / recovery**: `PRAGMA quick_check` on open; a corrupt file is moved to
  `events.db.corrupt-<timestamp>` (kept for manual recovery) and a fresh database is created.
- **Safe shutdown**: analysis threads close open motion episodes, capture stops, the writer drains the
  queue and checkpoints the WAL.

## Overlays

The agent draws overlays into the MJPEG preview: `?layers=regions,boxes,tracks,zones,lines` (or
`all`). Each layer is toggled independently in the camera details panel. Overlays show the latest
analysis result on the newest video frame, so boxes can lag the image by up to one analysis interval
(1 / `process_fps`).

## Resource management

- Bounded queues everywhere; analysis uses a 2-frame drop-oldest queue (automatic frame skipping) plus
  `process_fps`; analysis runs on a downscaled copy (`process_width`).
- CPU and memory of the agent process are monitored (`/system/status → resources`).
- Memory limit `ACI_AGENT_VISION_MAX_RSS_MB` (1024): above it all analysis pauses (`paused_memory`) while
  capture continues; it resumes below 90 % of the limit.
- Errors inside one camera's analysis are contained in that thread.

## Tests

`edge/camera-agent/tests` — synthetic scenes with known ground truth plus the real video pipeline:

| MVP 03 test item | Tests |
|---|---|
| Without internet / AI / server | `test_works_offline_without_ai_or_server` (socket connections blocked; no AI framework imported) |
| One moving object | `test_single_moving_object[*]`, `test_motion_started_and_ended_for_a_moving_object` |
| Several moving objects | `test_multiple_moving_objects[*]`, `test_multiple_objects_get_separate_tracks` |
| Image noise | `test_sensor_noise_is_not_motion[*]`, `test_noise_only_scene_produces_no_events` |
| Lighting change | `test_lighting_change_is_not_reported_as_motion[*]` |
| Empty frame | `test_empty_frame_is_ignored[*]` |
| Stream loss / camera recovery | `test_stream_loss_closes_motion_and_recovery_resumes` |
| Lines / zones | `test_line_crossing_*`, `test_zone_*`, `test_line_crossing_and_zone_events` |
| Storage | `test_event_store.py` (retention, size, disk, corruption, shutdown, overflow) |
| Resources | `test_frame_skipping_respects_process_fps`, `test_memory_limit_pauses_analysis_but_not_capture` |

Verified live: USB webcam + a demo video file; a line and a zone drawn in the dashboard produced
`line_crossed` → `object_entered_zone` → `object_exited_zone` sequences per track.
