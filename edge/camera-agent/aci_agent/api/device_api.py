"""Device management API: identity, hardware, central-server link, system log, backups."""

import time
from collections.abc import Callable
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from aci_agent import __version__
from aci_agent.agent.device import DeviceIdentity, hardware_info
from aci_agent.monitoring.hardware import HardwareMonitor
from aci_agent.monitoring.system_log import read_logs
from aci_agent.networking.client import InsecureServerURL
from aci_agent.security.auth import Principal
from aci_agent.storage.backup import list_backups
from aci_agent.storage.database import Database
from aci_agent.synchronization.manager import NetworkManager, RegistrationError


class DeviceRename(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class LinkRequest(BaseModel):
    server_url: str = Field(min_length=8, max_length=300)
    enrollment_token: str = Field(min_length=8, max_length=512)


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "details": None}})


def build_device_router(
    require: Callable[[str], Callable],
    *,
    identity: DeviceIdentity,
    hardware: HardwareMonitor,
    network: NetworkManager,
    db: Database,
    backups_dir,
    backup_now: Callable[[], dict],
    started_at: float,
    storage_stats: Callable[[], dict],
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/device", tags=["device"])
    CanRead = Annotated[Principal, Depends(require("devices:read"))]
    CanManage = Annotated[Principal, Depends(require("devices:manage"))]

    def _created_at() -> str | None:
        rows = db.query("SELECT created_at FROM device WHERE id = 1")
        return rows[0]["created_at"] if rows else None

    @router.get("")
    def get_device(_: CanRead):
        backups = list_backups(backups_dir)
        return {
            "device_id": identity.device_id,
            "device_name": identity.device_name,
            "created_at": _created_at(),
            "agent_version": __version__,
            "uptime_seconds": round(time.monotonic() - started_at, 1),
            "hardware": hardware_info(),
            "metrics": hardware.sample(),
            "sync": network.status(),
            "storage": storage_stats(),
            "backups": {"count": len(backups), "latest": backups[0] if backups else None},
        }

    @router.patch("")
    def rename(body: DeviceRename, _: CanManage):
        identity.rename(body.name)
        network.wake()  # next heartbeat carries the new name
        return {"device_id": identity.device_id, "device_name": identity.device_name}

    @router.get("/sync")
    def sync_status(_: CanRead):
        return network.status()

    @router.post("/sync")
    def sync_now(_: CanManage):
        network.wake()
        return network.status()

    @router.post("/link")
    async def link(body: LinkRequest, _: CanManage):
        try:
            return await anyio.to_thread.run_sync(network.register, body.server_url, body.enrollment_token)
        except InsecureServerURL as exc:
            return _err(422, "insecure_server_url", str(exc))
        except RegistrationError as exc:
            return _err(502, "registration_failed", str(exc))
        except ValueError as exc:
            return _err(422, "invalid_server_link", str(exc))

    @router.delete("/link")
    def unlink(_: CanManage):
        network.unlink()
        return network.status()

    @router.get("/logs")
    def logs(
        _: CanRead,
        level: Annotated[str | None, Query(max_length=10)] = None,
        component: Annotated[str | None, Query(max_length=60)] = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    ):
        try:
            return read_logs(db, level, limit, component)
        except ValueError as exc:
            return _err(422, "invalid_filter", str(exc))

    @router.get("/backups")
    def backups(_: CanRead):
        return list_backups(backups_dir)

    @router.post("/backups", status_code=201)
    async def create(_: CanManage):
        return await anyio.to_thread.run_sync(backup_now)

    return router
