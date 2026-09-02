"""vexa-mcp — the Vexa control MCP as a package.

``deploy/dogfood/rig/vexa_control_mcp.py`` was 5,033 lines in one file: 64 tools, a 187-line
instruction literal, a 740-line ASGI middleware, no ``pyproject.toml``, no tests and no CI — and it
could not start without a docker socket, two hardcoded container names, a Postgres URL and source
checkouts of two other trees on the filesystem (the seam inventory's B1 and B6). This package is the
same 64 tools, split by the domain each forwards to, with the four non-HTTP reaches gone and a test
that fails if any of them comes back.
"""

__version__ = "0.12.0"
