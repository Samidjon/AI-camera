"""Application factory."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, request_id_ctx
from app.db.session import set_engine

logger = logging.getLogger("aci.http")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.started_at = datetime.now(UTC)
        app.state.started_monotonic = time.monotonic()
        logger.info(
            "API starting",
            extra={"environment": settings.environment, "db_configured": settings.database_configured},
        )
        yield
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
