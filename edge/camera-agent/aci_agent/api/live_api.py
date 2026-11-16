"""Multiplexed live video over one WebSocket (MVP 07 monitoring wall).

Why: browsers allow ~6 HTTP/1.1 connections per host, so one MJPEG response per camera can't
show a 9- or 16-camera grid. Here every camera of a viewer shares one WebSocket; the agent pushes
only the newest frame per camera (frames are never queued — a slow viewer simply gets fewer
frames), JPEG-encoded once per frame / size / overlay variant and shared by all viewers.

Protocol (``/api/v1/live``):

1. client → ``{"type": "auth", "token": "<access token>"}`` (first message, ≤10 s; needs cameras:read)
2. client → ``{"type": "subscribe", "cameras": [ids], "layers": "regions,boxes,tracks,people",
   "max_fps": 1-15, "max_width": 160-1920}`` (any time; replaces the previous subscription)
3. server → binary frames: ``uint32 BE header length | header JSON | JPEG bytes``;
   header = ``{camera_id, seq, ts, width, height}``
4. server → ``{"type": "status", "cameras": {id: {status, fps, error, width, height}}}`` every second
"""

import asyncio
import contextlib
import json
import logging
import struct
import time
from collections.abc import Callable

import anyio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from aci_agent.camera.manager import CameraManager, CameraNotFoundError
from aci_agent.configuration.settings import AgentSettings
from aci_agent.security.auth import AuthError, principal_from_access_token
from aci_agent.standard_engine.overlay import parse_layers
from aci_agent.video.frame_buffer import LatestFrameBuffer

logger = logging.getLogger("aci.agent.live")
MAX_CAMERAS = 16


def _pack(header: dict, jpeg: bytes) -> bytes:
    h = json.dumps(header, separators=(",", ":")).encode()
    return struct.pack(">I", len(h)) + h + jpeg


def build_live_router(
    settings: AgentSettings,
    mgr: CameraManager,
    encode: Callable[[LatestFrameBuffer, object, str, frozenset, int], bytes],
) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["preview"])
    state = {"viewers": 0}

    @router.websocket("/live")
    async def live(ws: WebSocket) -> None:
        await ws.accept()
        try:
            first = await asyncio.wait_for(ws.receive_json(), timeout=10)
            token = first.get("token") if isinstance(first, dict) and first.get("type") == "auth" else ""
            principal = principal_from_access_token(str(token or ""), settings)
            if not principal.can("cameras:read"):
                raise AuthError("Missing permission: cameras:read", 403)
        except (TimeoutError, WebSocketDisconnect, ValueError, AuthError) as exc:
            with contextlib.suppress(Exception):
                await ws.send_json({"type": "error", "message": str(exc)[:200] or "unauthorized"})
                await ws.close(code=4401)
            return

        sub = {"cameras": [], "layers": frozenset(), "fps": 8.0, "width": 640}
        last_seq: dict[str, int] = {}
        last_sent: dict[str, float] = {}
        state["viewers"] += 1
        await ws.send_json({"type": "welcome", "max_cameras": MAX_CAMERAS})

        async def receiver() -> None:
            while True:
                msg = await ws.receive_json()
                if not isinstance(msg, dict) or msg.get("type") != "subscribe":
                    continue
                cams = [str(c)[:64] for c in (msg.get("cameras") or []) if isinstance(c, str)][:MAX_CAMERAS]
                sub["cameras"] = list(dict.fromkeys(cams))
                sub["layers"] = parse_layers(str(msg.get("layers") or "")) if msg.get("layers") else frozenset()
                sub["fps"] = max(1.0, min(15.0, float(msg.get("max_fps") or 8)))
                sub["width"] = max(160, min(1920, int(msg.get("max_width") or 640)))
                last_seq.clear()

        async def status_loop() -> None:
            while True:
                statuses = {}
                for cid in list(sub["cameras"]):
                    try:
                        rt = mgr.get_camera(cid).runtime
                    except CameraNotFoundError:
                        statuses[cid] = {"status": "not_found"}
                        continue
                    statuses[cid] = {"status": str(rt.status), "fps": rt.fps, "error": rt.last_error,
                                     "width": rt.width, "height": rt.height}  # fmt: skip
                await ws.send_json({"type": "status", "cameras": statuses})
                await asyncio.sleep(1.0)

        async def sender() -> None:
            while True:
                sent_any = False
                interval = 1.0 / sub["fps"]
                for cid in list(sub["cameras"]):
                    buffer = mgr.frame_buffer(cid)
                    frame = buffer.latest() if buffer else None
                    if frame is None or last_seq.get(cid) == frame.seq:
                        continue
                    now = time.monotonic()
                    if now - last_sent.get(cid, 0.0) < interval:
                        continue
                    jpeg = await anyio.to_thread.run_sync(encode, buffer, frame, cid, sub["layers"], sub["width"])
                    h, w = frame.image.shape[:2]
                    await ws.send_bytes(
                        _pack(
                            {"camera_id": cid, "seq": frame.seq, "ts": frame.timestamp, "width": w, "height": h}, jpeg
                        )
                    )  # awaiting the send is the backpressure: a slow client gets fewer frames, never a queue
                    last_seq[cid], last_sent[cid] = frame.seq, now
                    sent_any = True
                if not sent_any:
                    await asyncio.sleep(0.02)

        tasks = [asyncio.create_task(t()) for t in (receiver, status_loop, sender)]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                with contextlib.suppress(BaseException):
                    await t
            state["viewers"] -= 1
            with contextlib.suppress(Exception):
                await ws.close()

    router.live_state = state  # type: ignore[attr-defined]
    return router
