import platform
import time
from datetime import UTC, datetime

from fastapi import APIRouter, Request

from app import __version__
from app.api.deps import SettingsDep
from app.core.ratelimit import limiter
from app.realtime.hub import hub
from app.schemas.system import (
    ComponentState,
    OverallState,
    SystemStatusResponse,
    VersionResponse,
)
from app.services.health import check_database, check_redis, device_counts

router = APIRouter()


@router.get("/status", response_model=SystemStatusResponse, summary="Dependency health")
def system_status(request: Request, settings: SettingsDep) -> SystemStatusResponse:
    components = {
        "api": {"state": ComponentState.OK},
        "database": check_database(settings),
        "redis": check_redis(settings),
    }
    db_state = components["database"].state
    redis_state = components["redis"].state
    if db_state != ComponentState.OK:
        overall = OverallState.ERROR  # central server can't serve data without its DB
    elif redis_state == ComponentState.ERROR:
        overall = OverallState.DEGRADED  # Redis is optional
    else:
        overall = OverallState.OK

    started_mono: float = request.app.state.started_monotonic
    return SystemStatusResponse(
        status=overall,
        environment=settings.environment,
        uptime_seconds=round(time.monotonic() - started_mono, 1),
        started_at=request.app.state.started_at,
        timestamp=datetime.now(UTC),
        components=components,
        devices=device_counts(settings) if db_state == ComponentState.OK else None,
        event_processor=dict(request.app.state.event_processor.stats),
        realtime={"websocket_clients": hub.client_count, "published": hub.published, "rate_limited": limiter.rejected},
    )


@router.get("/version", response_model=VersionResponse, summary="Build / version info")
def system_version(settings: SettingsDep) -> VersionResponse:
    return VersionResponse(
        name=settings.app_name,
        version=__version__,
        api_version="v1",
        python_version=platform.python_version(),
        environment=settings.environment,
    )
