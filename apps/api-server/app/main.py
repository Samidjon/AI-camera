"""Application factory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import anyio
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, request_id_ctx
from app.db.session import session_scope, set_engine
from app.processing.event_processor import EventProcessor
from app.realtime.hub import hub
from app.services.devices import mark_stale_devices, prune_heartbeats

logger = logging.getLogger("aci.http")
monitor_logger = logging.getLogger("aci.devices.monitor")


def _monitor_once(settings: Settings, prune: bool) -> None:
    with session_scope(settings) as db:
        mark_stale_devices(db, settings)
        if prune:
            prune_heartbeats(db, settings)


async def device_monitor(settings: Settings) -> None:
    """Marks devices offline when their heartbeats stop; prunes old heartbeats hourly."""
    last_prune = 0.0
    while True:
        await asyncio.sleep(settings.device_monitor_interval_s)
        if not settings.database_configured:
            continue
        prune = time.monotonic() - last_prune > 3600
        try:
            await anyio.to_thread.run_sync(_monitor_once, settings, prune)
            if prune:
                last_prune = time.monotonic()
        except Exception as exc:  # noqa: BLE001 - the monitor must survive DB outages
            monitor_logger.warning("Device monitor pass failed: %s", type(exc).__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.started_at = datetime.now(UTC)
        app.state.started_monotonic = time.monotonic()
        hub.bind(asyncio.get_running_loop())
        monitor = asyncio.create_task(device_monitor(settings))
        logger.info(
            "API starting",
            extra={"environment": settings.environment, "db_configured": settings.database_configured},
        )
        yield
        monitor.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await monitor
        hub.bind(None)
        set_engine(None)  # dispose pool on shutdown
        logger.info("API stopped")

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="Central API server for AI Camera Intelligence. "
        "Edge agents work without this server (offline-first).",
        openapi_url=f"{settings.api_v1_prefix}/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )
    # Defaults so endpoints work even if lifespan isn't run (e.g. some test clients).
    app.state.started_at = datetime.now(UTC)
    app.state.started_monotonic = time.monotonic()
    app.state.settings = settings
    app.state.event_processor = EventProcessor(
        max_future_skew_s=settings.events_max_future_skew_s, max_age_days=settings.events_max_age_days
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next) -> Response:
        incoming = request.headers.get("X-Request-ID", "")
        rid = incoming if 0 < len(incoming) <= 64 and incoming.isprintable() else uuid.uuid4().hex
        token = request_id_ctx.set(rid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            response.headers["X-Request-ID"] = rid
            logger.info(
                "%s %s -> %s",
                request.method,
                request.url.path,
                response.status_code,
                extra={"duration_ms": duration_ms, "status": response.status_code},
            )
            return response
        finally:
            request_id_ctx.reset(token)

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
