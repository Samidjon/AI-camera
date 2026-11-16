"""Central server for the Playwright run: fresh SQLite database, three users, uvicorn on :18000.

Started by e2e/backend/supervisor.mjs (``uv run --project ../api-server``). With E2E_FRESH=1 the
database is recreated; a restart without it keeps the data (server outage / recovery test).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = HERE.parents[2] / "api-server"
DATA = HERE.parent / ".e2e-data"
DATA.mkdir(exist_ok=True)
DB = DATA / "central.db"
sys.path.insert(0, str(API))

os.environ.update(
    {
        "ACI_DATABASE_URL": f"sqlite:///{DB.as_posix()}",
        "ACI_ENVIRONMENT": "development",
        "ACI_JWT_SECRET": "e2e-jwt-secret-0123456789abcdef0123456789",
        "ACI_CORS_ORIGINS": '["http://localhost:13000"]',
        "ACI_LOG_JSON": "false",
        "ACI_LOG_LEVEL": "WARNING",
        "ACI_DEVICE_MONITOR_INTERVAL_S": "1",
        "ACI_RATE_HEARTBEAT_BURST": "1000",
    }
)

USERS = [
    ("admin@e2e.local", "administrator"),
    ("operator@e2e.local", "operator"),
    ("viewer@e2e.local", "viewer"),
]
PASSWORD = "e2e-password-123456"


def prepare() -> None:
    from alembic import command
    from alembic.config import Config

    for suffix in ("", "-wal", "-shm"):
        Path(f"{DB}{suffix}").unlink(missing_ok=True)
    cfg = Config(str(API / "alembic.ini"))
    cfg.set_main_option("script_location", str(API / "alembic"))
    cfg.attributes["database_url"] = os.environ["ACI_DATABASE_URL"]
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")

    from app.db.session import session_scope
    from app.services.auth import create_user

    with session_scope() as db:
        for email, role in USERS:
            create_user(db, email=email, password=PASSWORD, role_name=role, full_name=role.title())


if __name__ == "__main__":
    if os.environ.get("E2E_FRESH") == "1":
        prepare()
    import uvicorn

    from app.main import create_app

    uvicorn.run(create_app(), host="127.0.0.1", port=18000, log_level="warning")
