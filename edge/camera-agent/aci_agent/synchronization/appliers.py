"""Apply configuration pushed by the central server.

Each top-level key of the configuration document has one applier. Values were validated by the
server; they are validated again here (the agent never trusts input), and every change goes
through the same code paths as the local API, so it is persisted exactly as a local edit would be.

Keys: ``device_name``, ``heartbeat_interval_s`` (built into the network manager), ``ai_enabled``,
``events`` (retention / size limits), ``cameras`` (per camera: is_enabled, max_fps, ai_enabled,
motion settings, detection zones).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from aci_agent.ai_engine.engine import AIEngine
from aci_agent.camera.manager import CameraInput, CameraManager, CameraNotFoundError
from aci_agent.events.store import EventStore
from aci_agent.standard_engine.config import VisionConfig
from aci_agent.standard_engine.pipeline import VisionManager
from aci_agent.storage.vision_repository import VisionRepository

EVENT_LIMITS = {"retention_days": (1, 3650), "max_db_mb": (16, 1_048_576), "min_free_disk_mb": (50, 1_048_576)}
MOTION_KEYS = {"enabled", "algorithm", "sensitivity", "min_object_area", "process_fps", "motion_end_delay_s",
               "restrict_to_zones", "activity_interval_s"}  # fmt: skip


def _apply_events(store: EventStore, value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("events must be an object")
    unknown = set(value) - set(EVENT_LIMITS)
    if unknown:
        raise ValueError(f"unknown event settings: {', '.join(sorted(unknown))}")
    for key, raw in value.items():
        lo, hi = EVENT_LIMITS[key]
        v = int(raw)
        if not lo <= v <= hi:
            raise ValueError(f"{key} must be {lo}–{hi}")
        setattr(store.settings, key, v)


def _apply_camera(camera_id: str, s: dict, mgr: CameraManager, vision: VisionManager, repo: VisionRepository) -> None:
    cfg = mgr.get_camera(camera_id).config  # CameraNotFoundError for unknown ids
    options = dict(cfg.options)
    is_enabled = cfg.is_enabled
    changed = False
    if "is_enabled" in s and bool(s["is_enabled"]) != cfg.is_enabled:
        is_enabled, changed = bool(s["is_enabled"]), True
    if "max_fps" in s:
        fps = float(s["max_fps"])
        if not 0.5 <= fps <= 60:
            raise ValueError("max_fps must be 0.5–60")
        if options.get("max_fps") != fps:
            options["max_fps"], changed = fps, True
    if "ai_enabled" in s and options.get("ai_enabled", True) != bool(s["ai_enabled"]):
        options["ai_enabled"], changed = bool(s["ai_enabled"]), True
    if changed:
        mgr.update_camera(
            camera_id,
            CameraInput(name=cfg.name, source_type=cfg.source_type, source_uri=cfg.source_uri, location=cfg.location,
                        group_id=cfg.group_id, is_enabled=is_enabled, options=options),  # credentials kept
        )  # fmt: skip
    reload = False
    if "motion" in s:
        motion = s["motion"] or {}
        unknown = set(motion) - MOTION_KEYS
        if unknown:
            raise ValueError(f"unknown motion settings: {', '.join(sorted(unknown))}")
        current = repo.get_config(camera_id)
        repo.save_config(camera_id, VisionConfig.model_validate(current.model_dump() | motion))
        reload = True
    if "zones" in s:
        desired = {z["name"]: z for z in s["zones"] or []}
        existing = {z["name"]: z for z in repo.list_zones(camera_id)}
        for name, z in existing.items():
            if name not in desired:
                repo.delete_zone(camera_id, z["id"])
        for name, z in desired.items():
            repo.save_zone(camera_id, name, z.get("role", "monitor"), z["polygon"], bool(z.get("is_enabled", True)),
                           zone_id=existing[name]["id"] if name in existing else None)  # fmt: skip
        reload = True
    if reload:
        vision.reload(camera_id)


def _apply_cameras(value: Any, mgr: CameraManager, vision: VisionManager, repo: VisionRepository) -> None:
    if not isinstance(value, dict):
        raise ValueError("cameras must be an object keyed by camera id")
    errors = []
    for camera_id, settings in value.items():
        try:
            if not isinstance(settings, dict):
                raise ValueError("settings must be an object")
            _apply_camera(camera_id, settings, mgr, vision, repo)
        except CameraNotFoundError:
            errors.append(f"{camera_id}: camera not found on this device")
        except Exception as exc:  # noqa: BLE001 - one camera must not block the others
            errors.append(f"{camera_id}: {str(exc)[:150]}")
    if errors:
        raise ValueError("; ".join(errors))


def build_config_appliers(
    mgr: CameraManager, vision: VisionManager, repo: VisionRepository, ai: AIEngine, store: EventStore
) -> dict[str, Callable[[Any], None]]:
    return {
        "ai_enabled": lambda v: ai.set_enabled(bool(v)),
        "events": lambda v: _apply_events(store, v),
        "cameras": lambda v: _apply_cameras(v, mgr, vision, repo),
    }
