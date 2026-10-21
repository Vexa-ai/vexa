"""``python -m vexa_mcp`` — the production run entrypoint (compose CMD).

Serves ``create_app()`` (gateway base from env ``GATEWAY_URL``); HOST/PORT from env
(default port 8010, the compose-assigned MCP port).
"""
from __future__ import annotations

import logging
import os


def main() -> None:
    import uvicorn

    from .app import create_app

    # uvicorn configures only its own loggers; without a root handler at LOG_LEVEL the service's
    # own INFO lines (the per-call `mcp_tool_call` usage line among them) never reach stdout.
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "info").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    uvicorn.run(
        create_app(),
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8010")),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
