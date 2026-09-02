"""vexa-control — the dogfood lane's launcher for `core/mcp`.

THE SERVER MOVED AND THIS PATH DID NOT. `rig.sh` starts `$RIG_DIR/vexa_control_mcp.py`, `~/.storm`
carries a symlink to this exact file, and a rig is running from it right now. Deleting it would
stop the lane the moment this branch merged, for no gain — the 5,033 lines are gone either way, and
what is left is a launcher.

So this is a SHIM, not a fork: it adds `core/mcp/src` to the interpreter's path (the one path
manipulation in the whole seam, and it belongs in a launcher rather than in a server) and hands back
the package's ASGI app. `deploy/dogfood/rig/` now holds lane scripts and this, which is the shape
`deploy/dogfood/` is meant to have.

To run the product instead of the lane: `vexa-mcp` from the installed package, or the `mcp-control`
service in the stack. Both are the same code as this.
"""
from __future__ import annotations

import pathlib
import sys

_PKG = pathlib.Path(__file__).resolve().parents[3] / "core" / "mcp" / "src"
if _PKG.is_dir() and str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))

from vexa_mcp import config  # noqa: E402
from vexa_mcp.server import http_app  # noqa: E402

app = http_app()

if __name__ == "__main__":
    import uvicorn

    print(f"vexa-control MCP on http://127.0.0.1:{config.PORT}/mcp", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=config.PORT, log_level="warning")
