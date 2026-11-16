"""Edge agent for the Playwright run: fresh data directory, a generated video with a moving object
(real motion → real events), uvicorn on :18100. Cameras are added by the global setup via the API."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "edge" / "camera-agent"))
DATA = HERE.parent / ".e2e-data" / "agent"
shutil.rmtree(DATA, ignore_errors=True)
DATA.mkdir(parents=True)
VIDEO = DATA / "moving.mp4"


def make_video(path: Path, seconds: int = 12, fps: int = 15) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (640, 360))
    # 3 s an object crosses the scene, 3 s nothing moves: real motion_started / motion_ended cycles.
    for i in range(seconds * fps):
        frame = np.full((360, 640, 3), 40, np.uint8)
        phase = i % (6 * fps)
        if phase < 3 * fps:
            x = int(phase * 12)
            cv2.rectangle(frame, (x, 120), (x + 80, 260), (60, 160, 230), -1)
        writer.write(frame)
    writer.release()


make_video(VIDEO)
os.environ.update(
    {
        "ACI_AGENT_ENVIRONMENT": "development",
        "ACI_AGENT_DATA_DIR": str(DATA),
        "ACI_AGENT_PORT": "18100",
        "ACI_AGENT_JWT_SECRET": "e2e-jwt-secret-0123456789abcdef0123456789",
        "ACI_AGENT_CORS_ORIGINS": '["http://localhost:13000"]',
        "ACI_AGENT_LOG_JSON": "false",
        "ACI_AGENT_LOG_LEVEL": "WARNING",
        "ACI_AGENT_SERVER_ALLOW_INSECURE_HTTP": "true",
        "ACI_AGENT_SERVER_HEARTBEAT_INTERVAL_S": "5",
        "ACI_AGENT_RUNTIME_BACKUP_INTERVAL_H": "0",
        "ACI_AGENT_AI_MODELS_DIR": str(DATA / "models"),  # no model: AI fails safely if enabled
    }
)
(DATA / "video-path.txt").write_text(str(VIDEO), encoding="utf-8")

if __name__ == "__main__":
    import uvicorn

    from aci_agent.api.app import create_app
    from aci_agent.configuration.settings import AgentSettings

    settings = AgentSettings(_env_file=None)
    uvicorn.run(create_app(settings), host="127.0.0.1", port=18100, log_level="warning", log_config=None)
