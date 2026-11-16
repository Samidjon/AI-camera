"""AI control API: status, enable/disable, configuration, model registry, error log."""

from typing import Annotated

from fastapi import APIRouter, Depends

from aci_agent.ai_engine.configuration import AIRuntimeConfig
from aci_agent.ai_engine.engine import AIEngine
from aci_agent.camera.manager import CameraNotFoundError
from aci_agent.security.auth import Principal
from aci_agent.storage.ai_repository import AIRepository


def build_ai_router(require, ai: AIEngine, repo: AIRepository) -> APIRouter:
    router = APIRouter(prefix="/api/v1/ai", tags=["ai"])
    CanRead = Annotated[Principal, Depends(require("cameras:read"))]
    CanManage = Annotated[Principal, Depends(require("ai:manage"))]

    @router.get("/status")
    def status(_: CanRead):
        return ai.status()

    @router.post("/enable")
    def enable(_: CanManage):
        ai.set_enabled(True)
        return ai.status()

    @router.post("/disable")
    def disable(_: CanManage):
        ai.set_enabled(False)
        return ai.status()

    @router.get("/config", response_model=AIRuntimeConfig)
    def get_config(_: CanRead):
        return ai.config

    @router.put("/config", response_model=AIRuntimeConfig)
    def put_config(body: AIRuntimeConfig, _: CanManage):
        if ai.registry.get(body.model_id) is None:
            raise ValueError(f"Unknown model '{body.model_id}'")
        return ai.update_config(body)

    @router.get("/models")
    def models(_: CanRead):
        return [
            {
                "id": s.spec.id,
                "name": s.spec.name,
                "version": s.spec.version,
                "file": s.spec.filename,
                "size_bytes": s.spec.size_bytes,
                "sha256": s.spec.sha256,
                "task": s.spec.task,
                "license": s.spec.license,
                "source": s.spec.source,
                "status": s.status.value,
                "detail": s.detail,
                "checked_at": s.checked_at,
                "selected": s.spec.id == ai.config.model_id,
            }
            for s in ai.registry.list()
        ]

    @router.post("/models/{model_id}/validate")
    def validate(model_id: str, _: CanManage):
        if ai.registry.get(model_id) is None:
            raise CameraNotFoundError(model_id)
        s = ai.registry.validate(model_id)
        return {"id": model_id, "status": s.status.value, "detail": s.detail}

    @router.get("/errors")
    def errors(_: CanRead):
        return {"errors": list(ai.errors), "history": repo.history(50)}

    @router.get("/cameras/{camera_id}")
    def camera(camera_id: str, _: CanRead):
        return ai.camera_state(camera_id) or {"people": [], "frame_size": None}

    return router
