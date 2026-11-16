"""Run the edge agent:  uv run python -m aci_agent"""

from __future__ import annotations

import uvicorn

from aci_agent.api.app import create_app
from aci_agent.configuration.settings import get_settings


def main() -> None:
    settings = get_settings()
    app = create_app(settings)
    # Access log off: preview URLs carry short-lived stream tokens in the query string.
    uvicorn.run(app, host=settings.host, port=settings.port, access_log=False, log_config=None)


if __name__ == "__main__":
    main()
