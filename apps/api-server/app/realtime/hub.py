"""Real-time fan-out to dashboard WebSocket clients.

Messages: ``{"type": ..., "data": ..., "at": ISO-8601}``. Types:

* ``device.status``        device status / health changed (or a heartbeat arrived)
* ``camera.status``        a camera reported by a device changed status
* ``event.created``        events stored by the event processor (summary + up to 20 events)
* ``sync.status``          an upload / configuration sync finished
* ``system.notification``  human-readable notices (device offline, revoked, config failed …)

``publish`` is thread-safe (called from sync endpoints running in the thread pool). Each client
has a bounded queue; a slow client loses its oldest messages (counted), never blocks the server.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime

logger = logging.getLogger("aci.realtime")

# Permission a client needs to receive a message type.
TYPE_PERMISSION = {
    "device.status": "devices:read",
    "camera.status": "cameras:read",
    "event.created": "events:read",
    "sync.status": "devices:read",
    "system.notification": "devices:read",
}


@dataclass(eq=False)
class Client:
    permissions: set[str]
    subject: str
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=256))
    dropped: int = 0

    def can(self, permission: str) -> bool:
        return "*" in self.permissions or permission in self.permissions


class Hub:
    def __init__(self) -> None:
        self._clients: set[Client] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        self.published = 0

    def bind(self, loop: asyncio.AbstractEventLoop | None) -> None:
        self._loop = loop

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def register(self, client: Client) -> None:
        with self._lock:
            self._clients.add(client)

    def unregister(self, client: Client) -> None:
        with self._lock:
            self._clients.discard(client)

    def publish(self, type_: str, data: dict) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        message = {"type": type_, "data": data, "at": datetime.now(UTC).isoformat()}
        self.published += 1
        try:
            loop.call_soon_threadsafe(self._fanout, message)
        except RuntimeError:  # loop shutting down
            pass

    def notify(self, level: str, message: str, **context) -> None:
        self.publish("system.notification", {"level": level, "message": message, **context})

    def _fanout(self, message: dict) -> None:
        permission = TYPE_PERMISSION.get(message["type"], "devices:read")
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            if not client.can(permission):
                continue
            if client.queue.full():
                try:
                    client.queue.get_nowait()
                    client.dropped += 1
                except asyncio.QueueEmpty:
                    pass
            client.queue.put_nowait(message)


hub = Hub()
