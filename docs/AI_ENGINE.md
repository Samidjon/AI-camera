# AI Engine (MVP 04) — optional intelligence layer

The AI Engine adds person detection, tracking and visible-appearance attributes on top of the
[Standard Vision Engine](VISION_ENGINE.md). It is **optional and never required**: it is off by
default, and if it is disabled, missing a model, out of memory, timing out or crashing, video
capture, motion detection and local event storage keep running without a restart.

## Architecture

Package `edge/camera-agent/aci_agent/ai_engine/` (the spec's `edge/ai-engine` layout, placed inside
the edge agent as the MVP 05 structure prescribes):

| Module | Responsibility |
|---|---|
| `providers/base.py` | `AIProvider` interface: `initialize()`, `load_model()`, `predict()`, `health_check()`, `shutdown()` |
| `providers/opencv_yolox.py` | YOLOX-S (COCO-pretrained) on **OpenCV DNN** — no PyTorch / ONNX Runtime; person class only |
| `models/registry.py` | Model registry: id, version, file, pinned SHA-256, size, licence, source; status missing / unverified / invalid / valid / loaded |
| `tracking/bytetrack.py` | ByteTrack re-implementation (Kalman filter, two-stage high/low-confidence association, lifecycle) |
| `attributes/local_color.py` | On-device clothing colours (upper / lower) |
| `attributes/claude_provider.py` | Optional cloud attributes via Claude (clothing type, bag, backpack, headwear) |
| `monitoring/metrics.py` | Inference FPS, latency avg / p95, detections, timeouts, errors; NVIDIA GPU probe (`nvidia-smi`) |
| `configuration.py` | `ACI_AGENT_AI_*` environment settings + operator runtime config (persisted) |
| `engine.py` | Supervisor: modes, inference thread, watchdog, recovery with backoff, events |

Swapping the AI implementation means adding a provider; the engine, tracker, events, API and dashboard
don't change.

## Switching AI on and off

* **Globally** — AI Engine page, "AI enabled" switch (permission `ai:manage`). The top bar shows the AI
  state on every page.
* **Per camera** — camera details → "AI analysis for this camera" (`options.ai_enabled`, permission
  `cameras:manage`). Toggling it never reconnects the video stream; motion detection always keeps
  running on that camera.

## Modes

| Mode | Meaning |
|---|---|
| `disabled` | Turned off by the operator (default) |
| `loading` | Validating and loading the model |
| `active` | Running |
| `degraded` | Running with a limitation (e.g. GPU requested but unavailable → CPU; Claude selected without a key) |
| `offline` | Can't run: model file missing or failed checksum; re-checked every 30 s |
| `error` | Failed (inference error, timeout, memory, load failure); recovery scheduled |
| `recovering` | Recovery attempt in progress |

## Failure protection

On any AI failure the engine records the error (`GET /ai/errors`, status history), abandons the faulty
inference worker, leaves processing to the Standard Engine (which never stopped), keeps capture and
storage untouched, and retries with exponential backoff (`ACI_AGENT_AI_RECOVERY_INITIAL_S` →
`…_MAX_S`).

- **Inference timeout** (`ACI_AGENT_AI_INFERENCE_TIMEOUT_S`, 3 s): a watchdog abandons the inference
  thread if a call hangs.
- **Model health**: SHA-256 + size check, graph parse, warm-up inference and output-shape check before
  use; `health_check()` on every (re)load.
- **Memory**: refuses to load with less than `ACI_AGENT_AI_MIN_FREE_MEMORY_MB` free; goes to `error` if
  the agent exceeds `ACI_AGENT_AI_MAX_RSS_MB` while running.
- **GPU**: `ACI_AGENT_AI_BACKEND=cuda` uses CUDA only if OpenCV was built with it; otherwise it runs on CPU
  in `degraded` mode with a warning. GPU utilisation is shown when `nvidia-smi` exists.

## Detection, tracking, events

- YOLOX-S, input 640×640, configurable confidence threshold and minimum box height, NMS. Low-confidence
  boxes are kept for ByteTrack's second association stage.
- ByteTrack IDs `p-<camera>-<n>` are **temporary**: per camera, per session, never reused, never linked
  across cameras, never derived from appearance or faces.
- Events (`source = "ai"`): `person_detected` (track confirmed), `person_left` (left the view, lost,
  camera stream lost, or AI disabled — with duration and final attributes), `person_crossed_line`
  (same lines as the Standard Engine, with direction).

## Appearance attributes

Every attribute has `value`, `confidence` and `source`. Anything not reliable is `unknown`.

| Provider | Attributes | Where it runs |
|---|---|---|
| `off` | none | — |
| `local` (default) | upper / lower clothing colour | on device |
| `claude` | local colours + upper / lower clothing type, bag, backpack, headwear | Anthropic API (cloud) |

Local colours are only estimated for a full-body view (box height ≥ 1.6× width, not cut off by the
frame) and skin-tone pixels are excluded, so a skin colour is never reported as a clothing colour.

**Claude provider** (opt-in): one request per confirmed person track, rate-limited
(`ACI_AGENT_AI_CLAUDE_MAX_CALLS_PER_MINUTE`, 10), on a background worker with a bounded queue — it never
blocks detection. Only a downscaled crop (≤ 384 px) of that person is sent; nothing is stored by the
agent. The JSON schema allows only the seven clothing/accessory attributes; the system prompt forbids
identity, face, age, gender, ethnicity, body shape, emotion, health or any personal trait, and any
field outside the allow-list is dropped. Confidence values from Claude are model-reported. Model
`claude-opus-5-5` (`ACI_AGENT_AI_CLAUDE_MODEL`), low effort, server-side refusal fallback enabled. Any
failure (offline, timeout, refusal, invalid output) → `unknown`. **Check this fits your privacy policy
before enabling it.** The SDK is an optional extra: run the agent with
`uv run --extra claude python -m aci_agent` (plain `uv run` uninstalls extras; the AI status then shows
a warning and attributes stay local). The key is read only from `ACI_AGENT_AI_API_KEY` in `edge/camera-agent/.env`, never
logged or returned by the API.

## Installing the model

The agent never downloads models. Verified source: OpenCV Model Zoo on Hugging Face
(`huggingface.co/opencv/object_detection_yolox`), Apache-2.0:

| Registry id | File | Size | SHA-256 |
|---|---|---|---|
| `yolox_s_coco_2022nov` (default) | `object_detection_yolox_2022nov.onnx` | 35.9 MB | `c5c2d13e…be998063` |
| `yolox_s_coco_2022nov_int8` | `object_detection_yolox_2022nov_int8.onnx` | 9.1 MB | `01a3b0f4…baf03935` |

Place the file in `ACI_AGENT_AI_MODELS_DIR` (default `edge/camera-agent/models/`, git-ignored) and press
**Verify** on the AI Engine page.

## API (agent, `/api/v1/ai`)

| Method | Path | Permission |
|---|---|---|
| GET | `/status`, `/config`, `/models`, `/errors`, `/cameras/{id}` | `cameras:read` |
| POST | `/enable`, `/disable`, `/models/{id}/validate` | `ai:manage` (administrator) |
| PUT | `/config` | `ai:manage` |

Preview overlay layer `people` draws AI person boxes, temporary IDs, confidence and attributes.

## Measured on this development machine

Windows 10, CPU inference (OpenCV pip build has no CUDA; the machine's NVIDIA GPU is visible to
`nvidia-smi` but not used): YOLOX-S **~80–112 ms per frame**, ~8 inferences/s across 2 cameras at 4 FPS
each, agent RSS ~255 MB. These are this machine's numbers only; Jetson / mini-PC measurements belong to
MVP 09.

## Tests

`tests/unit/test_ai_components.py`, `tests/integration/test_ai_engine.py`, `tests/integration/test_ai_api.py`:

| MVP 04 item | Test |
|---|---|
| Start without AI | `test_starts_without_ai_and_standard_engine_works` |
| Start with AI | `test_starts_with_ai_and_tracks_people`, `test_real_model_loads_validates_and_runs` |
| Disable AI while running | `test_disable_ai_while_running` (+ verified live) |
| Corrupted model | `test_corrupted_model_goes_offline_without_affecting_capture`, `test_corrupted_model_file_is_rejected`, registry tests |
| Unavailable GPU | `test_unavailable_gpu_falls_back_to_cpu` (real), `test_unavailable_gpu_runs_degraded_on_cpu` |
| Insufficient memory | `test_insufficient_memory_refuses_to_load`, `test_memory_limit_while_running` |
| Inference timeout | `test_inference_timeout_is_contained_and_recovers` |
| AI recovery | `test_ai_crash_recovers_with_backoff_and_history`, `test_model_that_fails_to_load_is_error_then_recovers_when_fixed` |
| Standard Engine keeps working | asserted in the disable / corrupted / memory / timeout tests |
| Claude provider | request shape & privacy constraints, failures → unknown, rate limit, bounded queue (offline fakes; no paid calls in the suite) |
