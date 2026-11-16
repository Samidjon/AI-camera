"""Local management API of the edge agent (FastAPI).

Capture keeps running regardless of this API; the API is only the operator's control surface.
"""


import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import anyio
import cv2
from fastapi import APIRouter, Depends, FastAPI, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.exceptions import HTTPException as StarletteHTTPException

from aci_agent import __version__
from aci_agent.api.schemas import (
    AgentStatusOut,
    CameraCreate,
    CameraOut,
    CameraTestRequest,
    CameraUpdate,
    GroupIn,
    GroupOut,
    StreamTokenOut,
    TestResultOut,
)
from aci_agent.camera.manager import (
    CameraConflictError,
    CameraInput,
    CameraLimitError,
    CameraManager,
    CameraNotFoundError,
)
from aci_agent.camera.sources import CaptureFactory
from aci_agent.configuration.settings import AgentSettings
from aci_agent.monitoring.logging import configure_logging, redact
from aci_agent.security.auth import (
    AuthError,
    Principal,
    create_stream_token,
    principal_from_access_token,
    verify_stream_token,
)
from aci_agent.security.crypto import SecretBox
from aci_agent.storage.database import Database
from aci_agent.storage.repository import CameraRepository, DuplicateNameError
from aci_agent.video.frame_buffer import LatestFrameBuffer

logger = logging.getLogger("aci.agent.api")
_bearer = HTTPBearer(auto_error=False)
BOUNDARY = "acimjpegframe"


def _error(status_code: int, code: str, message: str, details=None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content={"error": {"code": code, "message": message, "details": details}}
    )


def create_app(
    settings: AgentSettings, factory: CaptureFactory | None = None, manager: CameraManager | None = None
) -> FastAPI:
    configure_logging(settings.log_level, settings.log_json)
    db: Database | None = None
    if manager is None:
        db = Database(settings.database_path)
        box = SecretBox.from_settings(settings.secret_key, settings.secret_key_path)
        manager = CameraManager(CameraRepository(db, box), settings, factory=factory)
    mgr = manager

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.started = time.monotonic()
        mgr.start()
        logger.info("Edge agent started", extra={"cameras": mgr.repo.count(), "auth": settings.auth_configured})
        try:
            yield
        finally:
            await anyio.to_thread.run_sync(mgr.shutdown)
            if db is not None:
                db.close()
            logger.info("Edge agent stopped")

    app = FastAPI(
        title="AI Camera Intelligence — Edge Agent",
        version=__version__,
        description="Local camera management API. Works without the central server or internet.",
        openapi_url="/api/v1/openapi.json",
        lifespan=lifespan,
    )
    app.state.started = time.monotonic()
    app.state.manager = mgr
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,  # cache preflight: status polling would otherwise double every request
    )

    # ------------------------------------------------------------ error handling

    @app.exception_handler(AuthError)
    async def _auth(_: Request, exc: AuthError):
        code = {401: "unauthorized", 403: "forbidden", 503: "auth_not_configured"}.get(exc.status_code, "unauthorized")
        return _error(exc.status_code, code, str(exc))

    @app.exception_handler(CameraNotFoundError)
    async def _nf(_: Request, exc: CameraNotFoundError):
        return _error(404, "not_found", "Camera or group not found")

    @app.exception_handler(CameraConflictError)
    async def _conflict(_: Request, exc: CameraConflictError):
        return _error(409, "conflict", str(exc))

    @app.exception_handler(DuplicateNameError)
    async def _dup(_: Request, exc: DuplicateNameError):
        return _error(409, "conflict", str(exc))

    @app.exception_handler(CameraLimitError)
    async def _limit(_: Request, exc: CameraLimitError):
        return _error(409, "camera_limit", str(exc))

    @app.exception_handler(ValueError)
    async def _value(_: Request, exc: ValueError):
        return _error(422, "invalid_camera", str(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        # Never echo submitted input back (it may contain a password).
        details = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return _error(422, "validation_error", "Request validation failed", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        return _error(exc.status_code, "not_found" if exc.status_code == 404 else "http_error", str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        logger.error("Unhandled error: %s", redact(repr(exc)))
        return _error(500, "internal_error", "Internal error")

    # ------------------------------------------------------------ auth deps

    def current_principal(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ) -> Principal:
        if credentials is None:
            raise AuthError("Not authenticated")
        return principal_from_access_token(credentials.credentials, settings)

    def require(permission: str):
        def guard(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
            if not principal.can(permission):
                raise AuthError(f"Missing permission: {permission}", 403)
            return principal

        return guard

    CanRead = Annotated[Principal, Depends(require("cameras:read"))]
    CanControl = Annotated[Principal, Depends(require("cameras:control"))]
    CanManage = Annotated[Principal, Depends(require("cameras:manage"))]

    router = APIRouter(prefix="/api/v1")

    # ------------------------------------------------------------ system

    @router.get("/health", tags=["system"])
    def health():
        return {"status": "ok"}

    @router.get("/system/status", response_model=AgentStatusOut, tags=["system"])
    def system_status():
        counts = mgr.status_counts()
        try:
            db_ok = mgr.repo._db.integrity_ok()
        except Exception:  # noqa: BLE001
            db_ok = False
        return AgentStatusOut(
            status="ok" if db_ok else "degraded",
            version=__version__,
            uptime_seconds=round(time.monotonic() - app.state.started, 1),
            auth_configured=settings.auth_configured,
            database_ok=db_ok,
            cameras_total=sum(counts.values()),
            cameras_by_status=counts,
            active_workers=mgr.active_workers(),
            leaked_workers=mgr.leaked_workers,
        )

    # ------------------------------------------------------------ cameras

    def _input_from_create(body: CameraCreate) -> CameraInput:
        return CameraInput(
            name=body.name, source_type=body.source_type, source_uri=body.source_uri, location=body.location,
            group_id=body.group_id, is_enabled=body.is_enabled,
            options=body.options.model_dump(exclude_none=True), username=body.username, password=body.password,
        )  # fmt: skip

    @router.get("/cameras", response_model=list[CameraOut], tags=["cameras"])
    def list_cameras(_: CanRead):
        return [CameraOut.from_view(v) for v in mgr.list_cameras()]

    @router.post("/cameras", response_model=CameraOut, status_code=status.HTTP_201_CREATED, tags=["cameras"])
    def create_camera(body: CameraCreate, who: CanManage):
        view = mgr.create_camera(_input_from_create(body), connect=body.connect)
        logger.info("Camera created by %s", who.subject, extra={"camera_id": view.config.id})
        return CameraOut.from_view(view)

    @router.get("/cameras/{camera_id}", response_model=CameraOut, tags=["cameras"])
    def get_camera(camera_id: str, _: CanRead):
        return CameraOut.from_view(mgr.get_camera(camera_id))

    @router.patch("/cameras/{camera_id}", response_model=CameraOut, tags=["cameras"])
    def update_camera(camera_id: str, body: CameraUpdate, who: CanManage):
        current = mgr.get_camera(camera_id).config
        fields = body.model_dump(exclude_unset=True)
        creds_given = "username" in fields or "password" in fields
        if body.clear_credentials:
            username, password = "", ""
        elif creds_given:
            existing = mgr.repo.get_secret(camera_id)
            username = body.username if "username" in fields else (existing.username if existing else "")
            password = body.password if "password" in fields else (existing.password if existing else "")
        else:
            username = password = None
        options = current.options
        if body.options is not None:
            options = {**current.options, **body.options.model_dump(exclude_none=True)}
        data = CameraInput(
            name=body.name if body.name is not None else current.name,
            source_type=body.source_type or current.source_type,
            source_uri=body.source_uri if body.source_uri is not None else current.source_uri,
            location=fields["location"] if "location" in fields else current.location,
            group_id=fields["group_id"] if "group_id" in fields else current.group_id,
            is_enabled=body.is_enabled if body.is_enabled is not None else current.is_enabled,
            options=options,
            username=username,
            password=password,
        )
        view = mgr.update_camera(camera_id, data)
        logger.info("Camera updated by %s", who.subject, extra={"camera_id": camera_id})
        return CameraOut.from_view(view)

    @router.delete("/cameras/{camera_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["cameras"])
    def delete_camera(camera_id: str, who: CanManage):
        mgr.delete_camera(camera_id)
        logger.info("Camera deleted by %s", who.subject, extra={"camera_id": camera_id})
        return Response(status_code=204)

    @router.post("/cameras/{camera_id}/connect", response_model=CameraOut, tags=["cameras"])
    def connect_camera(camera_id: str, _: CanControl):
        return CameraOut.from_view(mgr.connect(camera_id))

    @router.post("/cameras/{camera_id}/disconnect", response_model=CameraOut, tags=["cameras"])
    def disconnect_camera(camera_id: str, _: CanControl):
        return CameraOut.from_view(mgr.disconnect(camera_id))

    @router.post("/cameras/{camera_id}/test", response_model=TestResultOut, tags=["cameras"])
    def test_camera(camera_id: str, _: CanControl):
        return TestResultOut.from_result(mgr.test_existing(camera_id))

    @router.post("/cameras/test", response_model=TestResultOut, tags=["cameras"])
    def test_new_camera(body: CameraTestRequest, _: CanManage):
        data = CameraInput(
            name="test", source_type=body.source_type, source_uri=body.source_uri,
            options=body.options.model_dump(exclude_none=True), username=body.username, password=body.password,
        )  # fmt: skip
        return TestResultOut.from_result(mgr.test_input(data))

    # ------------------------------------------------------------ live preview (MJPEG)

    @router.post("/cameras/{camera_id}/stream-token", response_model=StreamTokenOut, tags=["preview"])
    def stream_token(camera_id: str, who: CanRead):
        mgr.get_camera(camera_id)
        token = create_stream_token(who.subject, camera_id, settings)
        base = f"/api/v1/cameras/{camera_id}"
        return StreamTokenOut(
            token=token,
            expires_in=settings.stream_token_ttl_s,
            stream_url=f"{base}/stream.mjpeg?token={token}",
            snapshot_url=f"{base}/snapshot.jpg?token={token}",
        )

    def _encode(buffer: LatestFrameBuffer, frame) -> bytes:
        s = settings.stream
        cached = buffer.cached_jpeg(frame.seq, s.preview_max_width)
        if cached is not None:
            return cached
        image = frame.image
        if image.shape[1] > s.preview_max_width:
            scale = s.preview_max_width / image.shape[1]
            image = cv2.resize(image, (s.preview_max_width, int(image.shape[0] * scale)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, s.preview_jpeg_quality])
        if not ok:
            raise RuntimeError("JPEG encoding failed")
        data = buf.tobytes()
        buffer.store_jpeg(frame.seq, s.preview_max_width, data)
        return data

    def _require_buffer(camera_id: str) -> LatestFrameBuffer:
        mgr.get_camera(camera_id)
        buffer = mgr.frame_buffer(camera_id)
        if buffer is None:
            raise CameraConflictError("Camera is not streaming — connect it first")
        return buffer

    @router.get("/cameras/{camera_id}/snapshot.jpg", tags=["preview"])
    def snapshot(camera_id: str, token: Annotated[str, Query(min_length=10)]):
        verify_stream_token(token, camera_id, settings)
        buffer = _require_buffer(camera_id)
        frame = buffer.latest() or buffer.wait_newer(0, 2.0)
        if frame is None:
            raise CameraConflictError("No frame available yet")
        return Response(_encode(buffer, frame), media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @router.get("/cameras/{camera_id}/stream.mjpeg", tags=["preview"])
    async def stream(camera_id: str, request: Request, token: Annotated[str, Query(min_length=10)]):
        verify_stream_token(token, camera_id, settings)
        buffer = _require_buffer(camera_id)
        min_interval = 1.0 / settings.stream.preview_max_fps
        viewer = uuid.uuid4().hex[:8]

        async def frames() -> AsyncIterator[bytes]:
            seq, last_sent = 0, 0.0
            try:
                while not await request.is_disconnected():
                    frame = await anyio.to_thread.run_sync(buffer.wait_newer, seq, 1.0)
                    if frame is None:
                        if mgr.frame_buffer(camera_id) is not buffer:
                            break  # camera disconnected / restarted: end this stream
                        continue
                    wait = min_interval - (time.monotonic() - last_sent)
                    if wait > 0:
                        await asyncio.sleep(wait)
                    seq, last_sent = frame.seq, time.monotonic()
                    data = await anyio.to_thread.run_sync(_encode, buffer, frame)
                    yield (
                        f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(data)}\r\n\r\n".encode()
                        + data
                        + b"\r\n"
                    )
            finally:
                logger.debug("Preview %s for camera %s closed", viewer, camera_id)

        return StreamingResponse(
            frames(),
            media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    # ------------------------------------------------------------ groups

    @router.get("/camera-groups", response_model=list[GroupOut], tags=["groups"])
    def list_groups(_: CanRead):
        return mgr.list_groups()

    @router.post("/camera-groups", response_model=GroupOut, status_code=201, tags=["groups"])
    def create_group(body: GroupIn, _: CanManage):
        return mgr.create_group(body.name, body.description)

    @router.patch("/camera-groups/{group_id}", response_model=GroupOut, tags=["groups"])
    def update_group(group_id: str, body: GroupIn, _: CanManage):
        return mgr.update_group(group_id, body.name, body.description)

    @router.delete("/camera-groups/{group_id}", status_code=204, tags=["groups"])
    def delete_group(group_id: str, _: CanManage):
        mgr.delete_group(group_id)
        return Response(status_code=204)

    app.include_router(router)
    return app
