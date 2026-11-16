"""Device authentication dependency (edge agents, not users)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.api.deps import DbDep, SettingsDep
from app.models import EdgeDevice
from app.services.devices import authenticate_device


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def get_current_device(request: Request, db: DbDep, settings: SettingsDep) -> EdgeDevice:
    return authenticate_device(
        db, settings, request.headers.get("authorization"), request.headers.get("x-aci-device-id")
    )


CurrentDevice = Annotated[EdgeDevice, Depends(get_current_device)]
