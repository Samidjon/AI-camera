"""Network discovery API (cameras:manage only)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from aci_agent.camera.discovery import DiscoveryService, ScanJob, local_private_networks
from aci_agent.camera.manager import CameraConflictError, CameraNotFoundError
from aci_agent.security.auth import Principal


class ScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    network: str | None = Field(
        default=None,
        max_length=32,
        description="Private CIDR, e.g. 192.168.1.0/24. Omit for the device's own networks.",
    )


def _job_out(job: ScanJob) -> dict:
    return {
        "id": job.id,
        "status": job.status,
        "progress": job.progress,
        "networks": job.networks,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "devices": job.devices,
        "error": job.error,
    }


def build_discovery_router(require, service: DiscoveryService) -> APIRouter:
    router = APIRouter(prefix="/api/v1/discovery", tags=["discovery"])
    CanManage = Annotated[Principal, Depends(require("cameras:manage"))]

    @router.get("/networks")
    def networks(_: CanManage):
        return [
            {"interface_ip": ip, "network": str(net), "hosts": net.num_addresses - 2}
            for ip, net in local_private_networks()
        ]

    @router.post("/scans", status_code=202)
    def start_scan(body: ScanRequest, who: CanManage):
        try:
            job = service.start(body.network)
        except RuntimeError as exc:
            raise CameraConflictError(str(exc)) from exc
        return _job_out(job)

    @router.get("/scans/latest")
    def latest(_: CanManage):
        job = service.latest()
        return _job_out(job) if job else None

    @router.get("/scans/{job_id}")
    def get_scan(job_id: str, _: CanManage):
        job = service.get(job_id)
        if job is None:
            raise CameraNotFoundError(job_id)
        return _job_out(job)

    @router.post("/scans/{job_id}/cancel")
    def cancel(job_id: str, _: CanManage):
        job = service.cancel(job_id)
        if job is None:
            raise CameraNotFoundError(job_id)
        return _job_out(job)

    return router
