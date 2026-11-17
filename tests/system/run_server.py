"""Central server process for the system tests: SQLite DB from ACI_DATABASE_URL, an administrator,
uvicorn on SYSTEM_TEST_PORT. SYSTEM_TEST_FRESH=1 migrates a new database; otherwise data is kept
(server outage / restart scenarios)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

API = Path(__file__).resolve().parents[2] / "apps" / "api-server"
sys.path.insert(0, str(API))


def prepare() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(API / "alembic.ini"))
    cfg.set_main_option("script_location", str(API / "alembic"))
    cfg.attributes["database_url"] = os.environ["ACI_DATABASE_URL"]
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")

    from app.db.session import session_scope
    from app.services.auth import create_user

    email, password = json.loads(os.environ["SYSTEM_TEST_ADMIN"])
    with session_scope() as db:
        create_user(db, email=email, password=password, role_name="administrator", full_name="System test")


if __name__ == "__main__":
    if os.environ.get("SYSTEM_TEST_FRESH") == "1":
        prepare()
    import uvicorn

    from app.main import create_app

    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ["SYSTEM_TEST_PORT"]), log_level="warning")
