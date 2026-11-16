"""Write the OpenAPI spec to shared/contracts/openapi.json (no database needed)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.core.config import Settings
from app.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[3]


def main() -> int:
    app = create_app(Settings(_env_file=None, environment="development", log_level="WARNING", log_json=False))
    out = REPO_ROOT / "shared" / "contracts" / "openapi.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
