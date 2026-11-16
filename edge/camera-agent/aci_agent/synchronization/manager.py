"""Network Manager: the agent's only link to the central server.

One background thread. Everything here is optional — when the server is unreachable, revoked or
never configured, cameras, analysis, AI and the local event database keep working exactly as
before; events simply wait in the bounded synchronization queue.

Cycle (while linked):

1. **Heartbeat** every ``heartbeat_interval_s``: identity, version, hardware metrics, camera / AI /
   queue status, applied configuration version. The reply carries the server's configuration
   version; if it is newer, the configuration is downloaded, applied and acknowledged.
2. **Event upload** in batches (oldest first). The server answers per event: ``accepted``,
   ``duplicate`` (already stored — idempotent by event UUID) or ``rejected`` (invalid, will never be
   accepted). Accepted and duplicate events leave the queue; rejected ones leave it and are counted;
   anything unconfirmed stays queued and is retried.
3. Failures → exponential backoff with jitter (``Retry-After`` honoured). 401 / 403 → the device is
   revoked: credentials are deleted, uploads stop, local operation continues.

States: ``unconfigured`` (no server), ``registering``, ``online``, ``offline`` (server unreachable —
retrying), ``revoked``, ``error`` (configuration problem, e.g. plain HTTP refused or enrollment
token rejected).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import httpx

from aci_agent import __version__
from aci_agent.agent.device import DeviceIdentity, hardware_info
from aci_agent.configuration.settings import ServerSettings
from aci_agent.events.store import EventStore
from aci_agent.networking.client import (
    DeviceRevoked,
    InsecureServerURL,
    ServerClient,
    ServerRejected,
    ServerUnavailable,
    validate_server_url,
)
from aci_agent.networking.link import ServerLinkRepository
from aci_agent.synchronization.backoff import Backoff

logger = logging.getLogger("aci.agent.sync")

EVENT_FIELDS = ("id", "camera_id", "event_type", "source", "occurred_at", "zone_id", "line_id", "track_id",
                "metadata", "created_at")  # fmt: skip


class LinkState(StrEnum):
    UNCONFIGURED = "unconfigured"
    REGISTERING = "registering"
    ONLINE = "online"
    OFFLINE = "offline"
    REVOKED = "revoked"
    ERROR = "error"


class RegistrationError(Exception):
    pass


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


class NetworkManager:
    def __init__(
        self,
        settings: ServerSettings,
        identity: DeviceIdentity,
        links: ServerLinkRepository,
        store: EventStore,
        *,
        status_provider: Callable[[], dict] | None = None,
        metrics_provider: Callable[[], dict] | None = None,
        config_appliers: dict[str, Callable[[Any], None]] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.identity = identity
        self.links = links
        self.store = store
        self._status_provider = status_provider or dict
        self._metrics_provider = metrics_provider or dict
        self._transport = transport
        self._appliers: dict[str, Callable[[Any], None]] = {
            "device_name": lambda v: self.identity.rename(str(v)),
            "heartbeat_interval_s": self._apply_heartbeat_interval,
            **(config_appliers or {}),
        }
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._client: ServerClient | None = None
        self._backoff = Backoff(settings.backoff_initial_s, settings.backoff_max_s)
        self._heartbeat_interval = settings.heartbeat_interval_s
        self._batch_size = settings.sync_batch_size
        self._next_heartbeat = 0.0
        self._next_attempt_at: float | None = None
        self._env_registration_failed = False
        self.state = LinkState.UNCONFIGURED
        self.reason: str | None = None
        self.last_error: str | None = None
        self.last_success_at: str | None = None
        self.uploaded = 0
        self.duplicates = 0
        self.rejected = 0
        self.heartbeats = 0
        self.config_applied: dict | None = None
        store.settings.sync_queue_max = settings.sync_queue_max
        link = links.get()
        # Queue new events only while there is a live link (otherwise the queue would fill for nothing).
        store.set_sync_enabled(bool(link and link.has_credentials and not link.revoked_at), clear_queue=False)

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="network-manager", daemon=True)
        self._thread.start()

    def shutdown(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    def wake(self) -> None:
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                delay = self.step()
            except Exception as exc:  # noqa: BLE001 - the loop must survive anything
                logger.exception("Network manager error: %s", type(exc).__name__)
                self._set_state(LinkState.OFFLINE, "internal error", str(exc)[:200])
                delay = self._backoff.next_delay()
            self._next_attempt_at = time.monotonic() + delay
            self._wake.wait(delay)
            self._wake.clear()

    # ------------------------------------------------------------------ state

    def _set_state(self, state: LinkState, reason: str | None = None, error: str | None = None) -> None:
        if state != self.state:
            log = logger.warning if state in (LinkState.OFFLINE, LinkState.REVOKED, LinkState.ERROR) else logger.info
            log("Central server link: %s → %s%s", self.state.value, state.value, f" ({reason})" if reason else "")
        self.state, self.reason = state, reason
        if error is not None:
            self.last_error = error

    def status(self) -> dict:
        link = self.links.get()
        now = time.monotonic()
        return {
            "state": self.state.value,
            "reason": self.reason,
            "last_error": self.last_error,
            "last_success_at": self.last_success_at,
            "next_attempt_in_s": round(max(0.0, self._next_attempt_at - now), 1) if self._next_attempt_at else None,
            "heartbeat_interval_s": self._heartbeat_interval,
            "heartbeats": self.heartbeats,
            "uploaded": self.uploaded,
            "duplicates": self.duplicates,
            "rejected": self.rejected,
            "queue": self.store.sync_stats(),
            "link": link.public() if link else None,
            "tls": {
                "https": bool(link and link.server_url.startswith("https://")),
                "custom_ca": self.settings.ca_file is not None,
            },
            "config_applied": self.config_applied,
        }

    # ------------------------------------------------------------------ registration

    def _make_client(self, url: str) -> ServerClient:
        return ServerClient(
            url, timeout_s=self.settings.request_timeout_s, ca_file=self.settings.ca_file, transport=self._transport
        )

    def register(self, server_url: str, enrollment_token: str) -> dict:
        """Register this device with a central server (one-time enrollment token)."""
        url = validate_server_url(server_url, self.settings.allow_insecure_http)
        token = enrollment_token.strip()
        if not 8 <= len(token) <= 512:
            raise ValueError("Enrollment token looks invalid")
        with self._lock:
            self._set_state(LinkState.REGISTERING)
            client = self._make_client(url)
            payload = {
                "device_id": self.identity.device_id,
                "device_name": self.identity.device_name,
                "agent_version": __version__,
                "hardware": hardware_info(),
            }
            try:
                response = client.register(token, payload)
            except ServerUnavailable as exc:
                client.close()
                self._set_state(LinkState.OFFLINE, "registration failed: server unreachable", str(exc))
                raise RegistrationError(str(exc)) from None
            except ServerRejected as exc:
                client.close()
                self._set_state(LinkState.ERROR, "registration rejected by the server", str(exc))
                raise RegistrationError(f"Registration rejected: {exc}") from None
            remote_id = str(response.get("device_id") or "")
            secret = str(response.get("device_secret") or "")
            if not remote_id or len(secret) < 16:
                client.close()
                self._set_state(LinkState.ERROR, "invalid registration response")
                raise RegistrationError("Server returned an invalid registration response")
            self.links.save_registration(url, remote_id, secret)
            if self._client is not None:
                self._client.close()
            client.set_credentials(remote_id, secret)
            self._client = client
            interval = response.get("heartbeat_interval_s")
            if interval is not None:
                self._apply_heartbeat_interval(interval)
            self.store.set_sync_enabled(True)
            self._backoff.reset()
            self._next_heartbeat = 0.0
            self._set_state(LinkState.ONLINE, "registered")
            self.last_error = None
            logger.info("Device registered with the central server", extra={"remote_device_id": remote_id})
        self.wake()
        return self.status()

    def unlink(self) -> None:
        """Forget the central server. Local data stays; queued uploads are discarded."""
        with self._lock:
            if self._client is not None:
                self._client.close()
                self._client = None
            self.links.delete()
            self.store.set_sync_enabled(False, clear_queue=True)
            self._set_state(LinkState.UNCONFIGURED, "unlinked by operator")
        logger.info("Device unlinked from the central server")

    # ------------------------------------------------------------------ main step

    def step(self) -> float:
        """One iteration; returns seconds until the next one. Public for deterministic tests."""
        with self._lock:
            link = self.links.get()
            if link is None:
                return self._step_unlinked()
            if link.revoked_at or not link.has_credentials:
                self.store.sync_enabled = False
                self._set_state(LinkState.REVOKED, "device revoked by the central server — re-register to resume")
                return 60.0
            if self._client is None or self._client.base_url != link.server_url:
                secret = self.links.secret()
                if secret is None:
                    self._set_state(LinkState.ERROR, "stored device credentials can't be decrypted")
                    return 60.0
                self._client = self._make_client(link.server_url)
                self._client.set_credentials(link.remote_device_id or self.identity.device_id, secret)
            try:
                now = time.monotonic()
                if now >= self._next_heartbeat:
                    self._heartbeat(link.config_version)
                    self._next_heartbeat = time.monotonic() + self._heartbeat_interval
                more = self._upload_batch()
            except ServerUnavailable as exc:
                self._set_state(LinkState.OFFLINE, "central server unreachable — events are queued locally", str(exc))
                return self._backoff.next_delay(exc.retry_after)
            except DeviceRevoked as exc:
                self.links.mark_revoked()
                self.store.set_sync_enabled(False, clear_queue=False)
                self._client.close()
                self._client = None
                self._set_state(LinkState.REVOKED, "device revoked by the central server", str(exc))
                return 60.0
            except ServerRejected as exc:
                self._set_state(LinkState.ERROR, "request rejected by the central server", str(exc))
                return self._backoff.next_delay()
            self._backoff.reset()
            self.last_success_at = _iso_now()
            self._set_state(LinkState.ONLINE)
            if more:
                return 0.05
            return max(0.05, min(self.settings.sync_interval_s, self._next_heartbeat - time.monotonic()))

    def _step_unlinked(self) -> float:
        s = self.settings
        if not s.url:
            self._set_state(LinkState.UNCONFIGURED, "no central server configured — standalone mode")
            return 30.0
        if not s.enrollment_token or self._env_registration_failed:
            self._set_state(
                self.state if self._env_registration_failed else LinkState.UNCONFIGURED,
                self.reason if self._env_registration_failed else "server URL set, waiting for an enrollment token",
            )
            return 30.0
        try:
            self.register(s.url, s.enrollment_token)
            return 0.05
        except InsecureServerURL as exc:
            self._env_registration_failed = True
            self._set_state(LinkState.ERROR, "plain HTTP refused", str(exc))
        except ValueError as exc:
            self._env_registration_failed = True
            self._set_state(LinkState.ERROR, "invalid server configuration", str(exc))
        except RegistrationError:
            if self.state == LinkState.ERROR:
                self._env_registration_failed = True  # token rejected: retrying it would never succeed
                return 30.0
            return self._backoff.next_delay()
        return 30.0

    # ------------------------------------------------------------------ heartbeat + configuration

    def _apply_heartbeat_interval(self, value: Any) -> None:
        interval = float(value)
        if not 5 <= interval <= 3600:
            raise ValueError("heartbeat_interval_s must be 5–3600")
        self._heartbeat_interval = interval

    def _heartbeat(self, applied_version: int) -> None:
        assert self._client is not None
        try:
            metrics = self._metrics_provider()
        except Exception as exc:  # noqa: BLE001 - a metrics failure must not block the heartbeat
            metrics = {"error": type(exc).__name__}
        try:
            extra = self._status_provider()
        except Exception as exc:  # noqa: BLE001
            extra = {"error": type(exc).__name__}
        payload = {
            "device_id": self.identity.device_id,
            "device_name": self.identity.device_name,
            "agent_version": __version__,
            "sent_at": _iso_now(),
            "config_version": applied_version,
            "hardware": hardware_info(),
            "metrics": metrics,
            "sync": self.store.sync_stats(),
            **extra,
        }
        reply = self._client.heartbeat(payload)
        self.heartbeats += 1
        self.links.touch("last_heartbeat_at")
        try:
            server_version = int(reply.get("config_version") or 0)
        except (TypeError, ValueError):
            server_version = 0
        if server_version > applied_version:
            self._sync_configuration()

    def _sync_configuration(self) -> None:
        assert self._client is not None
        doc = self._client.get_configuration()
        try:
            version = int(doc["version"])
        except (KeyError, TypeError, ValueError):
            raise ServerRejected("Configuration document has no valid version", 200) from None
        values = doc.get("config") or {}
        applied, ignored, errors = [], [], {}
        if isinstance(values, dict):
            for key, value in values.items():
                fn = self._appliers.get(key)
                if fn is None:
                    ignored.append(key)
                    continue
                try:
                    fn(value)
                    applied.append(key)
                except Exception as exc:  # noqa: BLE001 - one bad value must not block the rest
                    errors[key] = str(exc)[:200]
        result = "applied" if not errors else ("partial" if applied else "failed")
        # Record the version even when partial/failed: retrying an invalid document forever helps no one;
        # the server sees the outcome in the acknowledgement.
        self.links.set_config_version(version)
        self.config_applied = {"version": version, "status": result, "applied": applied,
                               "ignored": ignored, "errors": errors, "at": _iso_now()}  # fmt: skip
        logger.info("Configuration v%s from the central server: %s", version, result,
                    extra={"applied": applied, "ignored": ignored})  # fmt: skip
        self._client.ack_configuration(
            {"version": version, "status": result, "applied": applied, "ignored": ignored, "errors": errors}
        )

    # ------------------------------------------------------------------ event upload

    def _upload_batch(self) -> bool:
        """Upload one batch. Returns True when more events are waiting."""
        assert self._client is not None
        rows = self.store.fetch_unsynced(self._batch_size)
        if not rows:
            return False
        events = [{k: r.get(k) for k in EVENT_FIELDS} for r in rows]
        ids = [e["id"] for e in events]
        try:
            reply = self._client.upload_events(events)
        except ServerRejected as exc:
            if exc.status_code in (413, 422) and len(ids) > 1:
                self._batch_size = max(1, len(ids) // 2)  # shrink and retry: isolate a bad event
                self.store.mark_sync_failed(ids, str(exc))
                return True
            if exc.status_code in (413, 422):
                self.store.ack_synced(ids)  # a single event the server will never accept
                self.rejected += 1
                logger.warning("Event %s permanently rejected by the server: %s", ids[0], exc)
                return True
            self.store.mark_sync_failed(ids, str(exc))
            raise
        sent = set(ids)
        accepted = sent & {str(i) for i in reply.get("accepted") or []}
        duplicate = sent & {str(i) for i in reply.get("duplicate") or []}
        rejected: dict[str, str] = {}
        for item in reply.get("rejected") or []:
            if isinstance(item, dict) and str(item.get("id")) in sent:
                rejected[str(item["id"])] = str(item.get("reason") or "rejected")[:200]
        confirmed = accepted | duplicate | set(rejected)
        self.store.ack_synced(sorted(confirmed))
        unconfirmed = [i for i in ids if i not in confirmed]
        if unconfirmed:
            self.store.mark_sync_failed(unconfirmed, "not confirmed by the server")
        if rejected:
            logger.warning(
                "%d events rejected by the server", len(rejected), extra={"reasons": list(rejected.values())[:5]}
            )
        self.uploaded += len(accepted)
        self.duplicates += len(duplicate)
        self.rejected += len(rejected)
        more = len(rows) >= self._batch_size and not unconfirmed
        self._batch_size = min(self.settings.sync_batch_size, self._batch_size * 2)
        self.links.touch("last_sync_at")
        if unconfirmed and not confirmed:
            raise ServerUnavailable("Server confirmed none of the uploaded events")
        return more
