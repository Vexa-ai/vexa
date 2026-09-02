"""``vexa-mcp`` — the console entrypoint.

THE PRODUCT PATH IS STREAMABLE HTTP, and it is the default: this is a cloud edge, one service in
the stack, fronted by the gateway at ``/mcp``; a person's Claude Code or Codex connects to it over
HTTP with their own token (founder ruling, 2026-09-02). ``--stdio`` exists for the offline suites,
which drive the same server with no socket — it is a test transport, not a way to run Vexa on a
laptop.
"""
from __future__ import annotations

import sys

from . import config


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "serve":
        argv = argv[1:]
    if "--stdio" in argv:
        from .server import build
        build().run(transport="stdio")
        return 0
    import uvicorn
    from .server import http_app
    print(f"vexa-mcp on http://0.0.0.0:{config.PORT}/mcp", flush=True)
    uvicorn.run(http_app(), host="0.0.0.0", port=config.PORT, log_level="warning")  # noqa: S104
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
