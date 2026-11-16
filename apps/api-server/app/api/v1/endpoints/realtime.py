"""WebSocket for live dashboard updates.

Protocol: connect to ``/api/v1/ws``, then send ``{"type": "auth", "token": "<access token>"}`` within
10 s (the token is never put in the URL). The server answers ``{"type": "welcome", ...}`` and then
pushes messages (see ``app/realtime/hub.py``). ``{"type": "ping"}`` is answered with ``pong``; the
server also sends a ``ping`` every 25 s so idle proxies keep the connection.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid

import anyio
import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.security import decode_access_token
from app.db.session import session_scope
from app.models import User
from app.realtime.hub import Client, hub

logger = logging.getLogger("aci.realtime")
router = APIRouter()

CLOSE_UNAUTHORIZED = 4401


def _load_user(settings, token: str) -> tuple[str, set[str]] | None:
    try:
        payload = decode_access_token(token, settings)
        user_id = uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
    with session_scope(settings) as db:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            return None
        return str(user.id), set(user.role.permissions or [])


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    settings = websocket.app.state.settings
    await websocket.accept()
    try:
        first = await asyncio.wait_for(websocket.receive_json(), timeout=10)
    except (TimeoutError, WebSocketDisconnect, ValueError, KeyError):
        with contextlib.suppress(Exception):
            await websocket.close(code=CLOSE_UNAUTHORIZED)
        return
    token = first.get("token") if isinstance(first, dict) and first.get("type") == "auth" else None
    identity = await anyio.to_thread.run_sync(_load_user, settings, token) if isinstance(token, str) else None
    if identity is None:
        await websocket.send_json({"type": "error", "data": {"code": "unauthorized", "message": "Invalid token"}})
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return
    subject, permissions = identity
    client = Client(permissions=permissions, subject=subject)
    hub.register(client)
    await websocket.send_json({"type": "welcome", "data": {"subject": subject, "clients": hub.client_count}})

    async def sender() -> None:
        while True:
            try:
                message = await asyncio.wait_for(client.queue.get(), timeout=25)
            except TimeoutError:
                message = {"type": "ping"}
            await websocket.send_json(message)

    async def receiver() -> None:
        while True:
            msg = await websocket.receive_json()
            if isinstance(msg, dict) and msg.get("type") == "ping":
                await client.queue.put({"type": "pong"})

    tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(BaseException):
                await t  # retrieve the disconnect exception
        hub.unregister(client)
        with contextlib.suppress(Exception):
            await websocket.close()
