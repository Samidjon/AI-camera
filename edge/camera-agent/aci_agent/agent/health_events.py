"""Health transitions recorded as local events (source ``system``).

* ``camera_error``   a camera that was online goes to ``error`` / ``reconnecting`` (with the failure kind)
* ``camera_recovered`` it streams again
* ``ai_error``       the enabled AI engine goes to ``error`` / ``degraded`` / ``offline`` / ``recovering``
* ``ai_recovered``   it is ``active`` again

They are stored and synchronized like any other event, so the dashboard's Events page shows camera
and AI errors next to motion and person events, also for periods the server was unreachable.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from aci_agent.events.models import Event

logger = logging.getLogger("aci.agent.health")
BAD_CAMERA = {"error", "reconnecting"}
BAD_AI = {"error", "degraded", "offline", "recovering"}


class HealthEventMonitor:
    def __init__(
        self,
        cameras: Callable[[], list],  # -> CameraView list
        ai_state: Callable[[], tuple[bool, str, str | None]],  # -> (enabled, mode, reason)
        emit: Callable[[Event], object],
        interval_s: float = 2.0,
    ) -> None:
        self._cameras = cameras
        self._ai_state = ai_state
        self._emit = emit
        self._interval = interval_s
        self._camera_bad: dict[str, bool] = {}
        self._ai_bad = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="health-events", daemon=True)
            self._thread.start()

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(3)

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.check()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Health event check failed: %s", type(exc).__name__)

    def check(self) -> None:
        seen = set()
        for view in self._cameras():
            cid, rt = view.config.id, view.runtime
            seen.add(cid)
            status = str(rt.status)
            if status == "online":
                if self._camera_bad.get(cid):
                    self._emit(Event(camera_id=cid, event_type="camera_recovered", source="system",
                                     metadata={"camera_name": view.config.name}))  # fmt: skip
                self._camera_bad[cid] = False
            elif status in BAD_CAMERA and self._camera_bad.get(cid) is False:
                # Only after the camera was online in this run: no error storm at start-up.
                self._camera_bad[cid] = True
                self._emit(Event(camera_id=cid, event_type="camera_error", source="system", metadata={
                    "camera_name": view.config.name, "status": status,
                    "failure_kind": str(rt.failure_kind) if rt.failure_kind else None,
                    "error": (rt.last_error or "")[:200],
                }))  # fmt: skip
        for cid in set(self._camera_bad) - seen:
            del self._camera_bad[cid]

        enabled, mode, reason = self._ai_state()
        bad = enabled and mode in BAD_AI
        if bad and not self._ai_bad:
            self._emit(Event(camera_id="", event_type="ai_error", source="system",
                             metadata={"mode": mode, "reason": (reason or "")[:200]}))  # fmt: skip
        elif not bad and self._ai_bad and mode == "active":
            self._emit(Event(camera_id="", event_type="ai_recovered", source="system", metadata={"mode": mode}))
        if not bad and mode in ("active", "disabled"):
            self._ai_bad = False
        elif bad:
            self._ai_bad = True

    def fingerprint(self) -> tuple:
        """Compact state used to trigger an early heartbeat when something changes."""
        cams = tuple(sorted((v.config.id, str(v.runtime.status)) for v in self._cameras()))
        enabled, mode, _ = self._ai_state()
        return cams, enabled, mode
