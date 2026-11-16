from datetime import UTC, datetime

from fastapi import APIRouter

from app.schemas.system import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
def health() -> HealthResponse:
    """Process liveness only. Does not touch external dependencies, so it stays green
    while the database or Redis is down — use /system/status for dependency health."""
    return HealthResponse(status="ok", timestamp=datetime.now(UTC))
