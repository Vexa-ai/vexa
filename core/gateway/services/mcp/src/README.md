# src — mcp service source root

Holds the `vexa_mcp` Python package (the `pythonpath` for tests, per `pyproject.toml`).
The package is the front door; import `create_app` / `parse_meeting_url` from `vexa_mcp`,
never a deep module path (P6).
