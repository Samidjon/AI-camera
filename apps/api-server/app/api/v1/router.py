from fastapi import APIRouter

from app.api.v1.endpoints import auth, devices, events, health, realtime, system

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(system.router, prefix="/system", tags=["system"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(devices.router, prefix="/devices", tags=["devices"])
api_router.include_router(devices.groups_router, prefix="/device-groups", tags=["devices"])
api_router.include_router(events.router, prefix="/events", tags=["events"])
api_router.include_router(realtime.router, tags=["realtime"])
