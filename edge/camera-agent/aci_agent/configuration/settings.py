"""Agent configuration (environment variables with prefix ACI_AGENT_ or a local .env file)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class StreamSettings(BaseSettings):
    """Defaults for every camera worker. Values are bounded so a misconfiguration can't exhaust memory."""

    model_config = SettingsConfigDict(env_prefix="ACI_AGENT_STREAM_", extra="ignore")

    open_timeout_s: float = Field(default=8.0, ge=1, le=60)
    read_timeout_s: float = Field(default=5.0, ge=1, le=60)
    # A camera counts as ONLINE only while frames keep arriving within this window.
    stale_after_s: float = Field(default=4.0, ge=1, le=60)
    reconnect_initial_s: float = Field(default=1.0, ge=0.1, le=60)
    reconnect_max_s: float = Field(default=30.0, ge=1, le=600)
    frame_queue_size: int = Field(default=4, ge=1, le=64)
    preview_max_fps: float = Field(default=10.0, ge=0.5, le=30)
    preview_max_width: int = Field(default=960, ge=160, le=3840)
    preview_jpeg_quality: int = Field(default=75, ge=30, le=95)
    status_persist_interval_s: float = Field(default=30.0, ge=5, le=3600)


class VisionRuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ACI_AGENT_VISION_", extra="ignore")

    # Above this resident memory the agent pauses analysis (capture continues) until it drops.
    max_rss_mb: float = Field(default=1024, ge=128, le=65536)
    supervisor_interval_s: float = Field(default=2.0, ge=0.2, le=60)


class EventSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ACI_AGENT_EVENTS_", extra="ignore")

    retention_days: int = Field(default=30, ge=1, le=3650)
    max_db_mb: int = Field(default=512, ge=16, le=1_048_576)
    min_free_disk_mb: int = Field(default=500, ge=50, le=1_048_576)
    queue_size: int = Field(default=10_000, ge=100, le=1_000_000)
    maintenance_interval_s: float = Field(default=300, ge=1, le=86400)


class ServerSettings(BaseSettings):
    """Link to the central server (optional). Empty ``url`` = standalone device: nothing is uploaded and
    the agent never tries to reach the network."""

    model_config = SettingsConfigDict(env_prefix="ACI_AGENT_SERVER_", extra="ignore")

    url: str = ""
    # One-time enrollment token issued by the central server. Used only for the first registration;
    # afterwards the device authenticates with its own credentials (stored encrypted).
    enrollment_token: str = ""
    # Plain http:// is refused unless explicitly allowed (development / isolated lab networks only).
    allow_insecure_http: bool = False
    # Custom CA bundle for a server with a private certificate authority.
    ca_file: Path | None = None
    heartbeat_interval_s: float = Field(default=30, ge=5, le=3600)
    sync_interval_s: float = Field(default=5, ge=0.5, le=3600)
    sync_batch_size: int = Field(default=200, ge=1, le=1000)
    request_timeout_s: float = Field(default=10, ge=1, le=120)
    backoff_initial_s: float = Field(default=2, ge=0.1, le=600)
    backoff_max_s: float = Field(default=300, ge=1, le=86400)
    sync_queue_max: int = Field(default=200_000, ge=1000, le=10_000_000)


class RuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ACI_AGENT_RUNTIME_", extra="ignore")

    system_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    system_log_max_rows: int = Field(default=5000, ge=100, le=1_000_000)
    backup_keep: int = Field(default=7, ge=1, le=365)
    # Automatic local backup of the databases every N hours (0 = off).
    backup_interval_h: float = Field(default=24, ge=0, le=24 * 30)


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", env_prefix="ACI_AGENT_", extra="ignore"
    )

    environment: Literal["development", "test", "production"] = "development"
    host: str = "127.0.0.1"
    port: int = Field(default=8100, ge=1, le=65535)
    data_dir: Path = Path("./data")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True

    # Shared with the central server (ACI_JWT_SECRET) so dashboard tokens are verified locally,
    # without any network call. Empty = management API disabled; capture keeps running.
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    stream_token_ttl_s: int = Field(default=120, ge=10, le=3600)

    # Optional explicit Fernet key for camera secrets; otherwise a key file is created in data_dir.
    secret_key: str = ""

    cors_origins: list[str] = ["http://localhost:3000"]
    # Optional regex for additional dashboard origins (e.g. a Vercel deployment and its previews).
    cors_origin_regex: str = ""
    max_cameras: int = Field(default=32, ge=1, le=256)
    autostart_cameras: bool = True

    stream: StreamSettings = Field(default_factory=StreamSettings)
    vision: VisionRuntimeSettings = Field(default_factory=VisionRuntimeSettings)
    events: EventSettings = Field(default_factory=EventSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    # Device name shown on the central server (default: host name). Can be renamed later.
    device_name: str = ""

    @property
    def events_database_path(self) -> Path:
        return self.data_dir / "events.db"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "agent.db"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def secret_key_path(self) -> Path:
        return self.data_dir / "secret.key"

    @property
    def auth_configured(self) -> bool:
        return len(self.jwt_secret) >= 16


@lru_cache
def get_settings() -> AgentSettings:
    return AgentSettings()
