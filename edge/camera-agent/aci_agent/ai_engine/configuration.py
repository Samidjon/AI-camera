"""AI settings.

Static settings come from the environment (``ACI_AGENT_AI_*``). Runtime choices an operator makes
in the dashboard (enabled, model, thresholds, attribute provider) are stored in the local
database and survive restarts.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AttributeProviderKind(StrEnum):
    OFF = "off"
    LOCAL = "local"  # on-device colour analysis; other attributes "unknown"
    CLAUDE = "claude"  # local colours + Claude for clothing type / bag / backpack / headwear (cloud)


class AIEnvSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ACI_AGENT_AI_", extra="ignore")

    models_dir: Path = Path("./models")
    # Backend preference: "cpu" or "cuda". CUDA falls back to CPU (reported) when unavailable.
    backend: str = Field(default="cpu", pattern="^(cpu|cuda)$")
    inference_timeout_s: float = Field(default=3.0, ge=0.2, le=60)
    recovery_initial_s: float = Field(default=2.0, ge=0.1, le=600)
    recovery_max_s: float = Field(default=120.0, ge=1, le=3600)
    # Refuse to load a model when the system has less free memory than this.
    min_free_memory_mb: int = Field(default=512, ge=0, le=1_048_576)
    # Above this agent RSS the AI unloads its model (degraded) — capture and Standard Engine continue.
    max_rss_mb: float = Field(default=2048, ge=128, le=1_048_576)

    # Optional cloud attribute provider (Claude). Never required; used only if selected.
    api_key: SecretStr = SecretStr("")
    claude_model: str = "claude-opus-5-5"
    claude_timeout_s: float = Field(default=20.0, ge=2, le=120)
    claude_max_calls_per_minute: int = Field(default=10, ge=1, le=600)


class AIRuntimeConfig(BaseModel):
    """Operator-controlled AI configuration (persisted)."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    enabled: bool = False  # AI is opt-in
    model_id: str = "yolox_s_coco_2022nov"
    confidence_threshold: float = Field(default=0.45, ge=0.05, le=0.95)
    nms_threshold: float = Field(default=0.5, ge=0.1, le=0.9)
    min_box_height: float = Field(default=0.06, ge=0.0, le=0.9, description="Fraction of frame height")
    process_fps: float = Field(default=4.0, ge=0.5, le=30)
    attributes: AttributeProviderKind = AttributeProviderKind.LOCAL
    # ByteTrack
    track_high_threshold: float = Field(default=0.5, ge=0.1, le=0.95)
    track_buffer_frames: int = Field(default=30, ge=1, le=300)
    min_track_hits: int = Field(default=3, ge=1, le=30)
